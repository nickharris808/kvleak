"""kvleak.cli — the command line.

    kvleak residency --model MODEL --prefixes N --tokens T --kv-budget-gib G
    kvleak plan      --model MODEL --kv-budget-gib G      what probe set IS interpretable here
    kvleak scan      --engine vllm --base-url URL         the live probe (needs an endpoint)
    kvleak selftest                                        positive controls for the tool itself

`residency` and `plan` need no GPU and no endpoint -- they are arithmetic over a model config,
and they are what stop a live scan producing a meaningless all-clear.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .probes import UNFIXED_NOTICE
from .residency import ModelKV, check_residency, max_interpretable_prefixes

# Published configs for two models that differ ONLY in grouped-query attention, which is the
# variable that dominates KV footprint. Sourced from each model's config.json.
KNOWN = {
    "qwen2.5-1.5b": ModelKV("Qwen2.5-1.5B-Instruct", 28, 2, 128, "fp16"),
    "phi-3-mini-4k": ModelKV("Phi-3-mini-4k-instruct", 32, 32, 96, "fp16"),
    "llama-3.1-8b": ModelKV("Llama-3.1-8B-Instruct", 32, 8, 128, "fp16"),
    "qwen2.5-7b": ModelKV("Qwen2.5-7B-Instruct", 28, 4, 128, "fp16"),
}


def _resolve(name: str, cfg_path: str | None) -> ModelKV:
    if cfg_path:
        with open(cfg_path, encoding="utf-8") as fh:
            return ModelKV.from_config(name or cfg_path, json.load(fh))
    key = (name or "").lower()
    if key in KNOWN:
        return KNOWN[key]
    # EXIT 2, not 1. In this portfolio 1 means "checked, and the property does not hold" --
    # here, "your probe set is not interpretable". Not knowing the model is a different thing
    # entirely: nothing was checked at all, and conflating the two tells a user their probe is
    # unusable when in fact the tool just needs a --config.
    print(f"unknown model {name!r}. Either pass --config path/to/config.json (any HuggingFace "
          f"config.json works) or use one of: {', '.join(sorted(KNOWN))}", file=sys.stderr)
    raise SystemExit(2)


def _cmd_residency(a) -> int:
    model = _resolve(a.model, a.config)
    budget = int(a.kv_budget_gib * 2 ** 30)
    v = check_residency(model, n_prefixes=a.prefixes, tokens_each=a.tokens,
                        kv_budget_bytes=budget, safety_margin=a.safety_margin)
    if a.json:
        print(json.dumps({"model": model.name, "bytes_per_token": model.bytes_per_token,
                          **v.as_dict()}, indent=2))
        return 0 if v.interpretable else 1
    print(f"model .............. {model.name}")
    print(f"  layers ........... {model.num_hidden_layers}")
    print(f"  KV heads ......... {model.num_key_value_heads}"
          + ("" if model.num_key_value_heads > 1 else "   (no GQA — worst case for KV footprint)"))
    print(f"  KV per token ..... {model.bytes_per_token:,} bytes")
    print()
    print(f"probe set .......... {a.prefixes} prefixes x {a.tokens} tokens")
    print(f"  requires ......... {v.required_bytes / 2**30:.2f} GiB")
    print(f"  available ........ {v.available_bytes / 2**30:.2f} GiB")
    print(f"  fits ............. {v.resident_prefixes} of {v.requested_prefixes}")
    print()
    print(f"  INTERPRETABLE: {v.interpretable}")
    print(f"  {v.reason}")
    if not v.interpretable:
        n = max_interpretable_prefixes(model, tokens_each=a.tokens, kv_budget_bytes=budget)
        print(f"\n  Largest probe set that WOULD be interpretable here: {n} prefixes.")
    return 0 if v.interpretable else 1


def _cmd_plan(a) -> int:
    model = _resolve(a.model, a.config)
    budget = int(a.kv_budget_gib * 2 ** 30)
    print(f"{model.name}: {model.bytes_per_token:,} bytes of KV per token\n")
    print(f"{'prefix length':>14} {'max interpretable prefixes':>28}")
    for tokens in (256, 512, 1024, 1800, 4096, 8192):
        print(f"{tokens:>14} {max_interpretable_prefixes(model, tokens_each=tokens, kv_budget_bytes=budget):>28}")
    print("\nA probe set larger than these is not a stronger test -- it is an uninterpretable one.")
    return 0


def _cmd_scan(a) -> int:
    if a.include_unfixed:
        print(f"!! {UNFIXED_NOTICE}\n")
    print("kvleak scan requires a reachable serving endpoint.")
    print(f"  engine ....... {a.engine}")
    print(f"  base URL ..... {a.base_url or '(none given)'}")
    if not a.base_url:
        print("\nNo --base-url supplied, so nothing was probed and nothing is reported.")
        print("Run `kvleak residency ...` first: it needs no endpoint and tells you whether a")
        print("scan on this model and cache budget could produce an interpretable result at all.")
        return 2
    print("\nLive probing is not performed in this build: the HTTP probe path is deliberately")
    print("not exercised without an endpoint, and this tool refuses to print a result it did")
    print("not measure. See README 'Live scanning' for the supported harness.")
    return 2


def _cmd_selftest(a) -> int:
    """Positive controls for the tool itself: a tool that cannot fail is not a check.

    The 208-token fixture value is a real measured figure, not an invention: it is the
    cross-tenant reuse recorded in `results/data/statefabric/gpu/inengine_weave.json`
    (registered as `cross_tenant_tokens_unsalted` in oss/provenance.py). It is used here as a
    realistic classifier input, and asserts nothing about any other deployment.
    """
    from .probes import evaluate_block_conformance, evaluate_reuse
    failures = []

    r = evaluate_reuse(208, 208, 0, 0, 0)
    if r.outcome != "LEAK":
        failures.append(f"a real leak was not classified as LEAK (got {r.outcome})")
    r = evaluate_reuse(0, 208, 0, 0, 0)
    if r.outcome != "CLEAN":
        failures.append(f"a clean result was not classified CLEAN (got {r.outcome})")
    r = evaluate_reuse(0, 0, 0, 0, 0)
    if r.outcome != "INCONCLUSIVE":
        failures.append(f"caching-off must be INCONCLUSIVE, not CLEAN (got {r.outcome})")
    r = evaluate_reuse(208, 208, 0, 64, 0)
    if r.outcome != "INCONCLUSIVE":
        failures.append(f"self-inflicted residency must be INCONCLUSIVE (got {r.outcome})")
    if evaluate_block_conformance([16, 32, 48], 16).outcome != "CLEAN":
        failures.append("conforming block counts misclassified")
    if evaluate_block_conformance([16, 33], 16).outcome != "LEAK":
        failures.append("non-conforming block count not caught")

    phi = KNOWN["phi-3-mini-4k"]
    v = check_residency(phi, n_prefixes=30, tokens_each=1800, kv_budget_bytes=int(5.6 * 2 ** 30))
    if v.interpretable:
        failures.append("the known-uninterpretable case was reported interpretable")

    for f in failures:
        print(f"  FAIL  {f}")
    if failures:
        print(f"\n{len(failures)} positive control(s) failed.")
        return 1
    print("  ok    leak / clean / caching-off / self-inflicted all classified correctly")
    print("  ok    block-quantisation conformance detects a non-multiple")
    print("  ok    the known-uninterpretable probe set is refused")
    print("\nselftest passed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="kvleak",
        description="does your serving stack leak between tenants? (measure-only)")
    ap.add_argument("--version", action="version", version=f"kvleak {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def _model_args(p):
        p.add_argument("--model", default="", help="a known name, or a label for --config")
        p.add_argument("--config", help="path to a HuggingFace config.json")
        p.add_argument("--kv-budget-gib", type=float, required=True,
                       help="GiB of KV cache the engine actually has (after weights)")

    r = sub.add_parser("residency", help="can a null from this probe set even be interpreted?")
    _model_args(r)
    r.add_argument("--prefixes", type=int, default=30)
    r.add_argument("--tokens", type=int, default=1800)
    r.add_argument("--safety-margin", type=float, default=1.0)
    r.add_argument("--json", action="store_true")
    r.set_defaults(fn=_cmd_residency)

    p = sub.add_parser("plan", help="what probe set IS interpretable on this model and budget")
    _model_args(p)
    p.set_defaults(fn=_cmd_plan)

    s = sub.add_parser("scan", help="live cross-tenant probe (needs an endpoint)")
    s.add_argument("--engine", choices=["vllm", "sglang"], default="vllm")
    s.add_argument("--base-url")
    s.add_argument("--include-unfixed", action="store_true",
                   help="enable probe 4, which automates an UNFIXED upstream defect")
    s.set_defaults(fn=_cmd_scan)

    t = sub.add_parser("selftest", help="positive controls for the tool itself")
    t.set_defaults(fn=_cmd_selftest)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
