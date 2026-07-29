# kvleak

**Your tenant-isolation test passed. Were the victim's prefixes still in the cache when you ran it?**

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](pyproject.toml)

A black-box scanner for cross-caller prefix reuse on multi-tenant LLM serving stacks — with the
thing these probes usually lack: **a residency precondition that refuses to report a null it
cannot interpret.**

```bash
pip install kvleak
```

## The bug in every such test, including ours

A cross-tenant probe works like this: a victim sends prefixes, then an attacker re-sends them and
looks for a cache hit. See nothing, conclude no leak.

That conclusion is wrong whenever the victim's prefixes were **evicted before the attacker
probed**. You then measured two cache misses and compared them. The null is real; it just says
nothing about isolation.

We found this by replicating our own published timing result on a second model. It produced a
beautifully clean null — hit and control medians identical to three decimal places. The cause was
not isolation:

```console
$ kvleak residency --model phi-3-mini-4k --prefixes 30 --tokens 1800 --kv-budget-gib 5.6
model .............. Phi-3-mini-4k-instruct
  layers ........... 32
  KV heads ......... 32
  KV per token ..... 393,216 bytes

probe set .......... 30 prefixes x 1800 tokens
  requires ......... 19.78 GiB
  available ........ 5.60 GiB
  fits ............. 8 of 30

  INTERPRETABLE: False
  only ~8 of 30 primed prefixes can be resident (19.78 GiB needed, 5.60 GiB available). The
  victim's state would be EVICTED before the attacker probes, so a null measures two cache
  misses and means nothing. REFUSING to report an all-clear.

  Largest probe set that WOULD be interpretable here: 8 prefixes.
```

The model has **no grouped-query attention** — 32 KV heads against 32 attention heads — so it
stores 13.7× more KV per token than the model we first tested on. Same probe set, same cache
budget, on a GQA model:

```console
$ kvleak residency --model qwen2.5-1.5b --prefixes 30 --tokens 1800 --kv-budget-gib 5.6
  KV per token ..... 28,672 bytes
  requires ......... 1.44 GiB
  INTERPRETABLE: True
```

**This is a precondition of the published attack that nobody states: the victim's state must
still be resident.** Under cache pressure the attack silently stops working — and a scanner that
does not check will hand you a clean bill of health for a system that is leaking.

## Install

```bash
pip install kvleak          # zero runtime dependencies
```

## 30-second quickstart

```bash
# 1. Can a null from this probe set even be interpreted on your model?  (no GPU, no endpoint)
kvleak residency --model qwen2.5-1.5b --prefixes 30 --tokens 1800 --kv-budget-gib 5.6

# 2. What probe set IS interpretable on your model and cache budget?
kvleak plan --config ./config.json --model mine --kv-budget-gib 12

# 3. Does the tool itself still detect the things it claims to?
kvleak selftest

# 4. The live probe (needs a reachable serving endpoint)
kvleak scan --engine vllm --base-url http://localhost:8000
```

Steps 1–3 need no GPU and no endpoint. **Run step 1 before step 4**, or you will not know whether
a clean scan means anything. Any HuggingFace `config.json` works via `--config`.

## The four controls

A cross-tenant measurement without controls is unreadable. Every result carries all four, and
each kills one alternative explanation:

| control | what it rules out |
|---|---|
| **C1** cold baseline | a "fast" response with nothing to be fast relative to |
| **C2** same-caller reuse | *caching was never on*, which makes a cross-tenant zero vacuous |
| **C3** anti-carryover | **the harness created the residency it then "discovered"** |
| **C4** length-matched | "it was cached" confused with "it was shorter" |

C3 is the one that bites hardest. A probe harness that primes and measures in the same engine can
manufacture its own positive, and we have had that happen — which is why a non-zero anti-carryover
reading yields `INCONCLUSIVE` and not a finding.

C2 is the one that produces false comfort. Zero cross-tenant reuse *because there is no reuse at
all* is not isolation:

```python
>>> evaluate_reuse(cross_tenant_cached=0, same_tenant_cached=0, cold_baseline_cached=0,
...                anti_carryover_cached=0, length_matched_cached=0).outcome
'INCONCLUSIVE'
```

## What a real leak looks like

The `208` below is not a made-up number: it is the cross-tenant reuse we measured on a real
engine, recorded in `results/data/statefabric/gpu/inengine_weave.json` and registered in
`oss/provenance.py` as `cross_tenant_tokens_unsalted`. It is used here as the shape of a
positive, not as a claim about your stack.

```python
>>> r = evaluate_reuse(cross_tenant_cached=208, same_tenant_cached=208,
...                    cold_baseline_cached=0, anti_carryover_cached=0, length_matched_cached=0)
>>> r.outcome
'LEAK'
>>> r.detail
"a caller with a DIFFERENT identity was served 208 cached tokens of another caller's
 byte-identical prefix, while an unsent length-matched prefix got 0. The cache is keyed on
 content and not on identity."
```

## Probes, in disclosure-safety order

1. **Cross-caller prefix reuse** — black-box, through public generate paths, with the four
   controls. Supports vLLM and SGLang.
2. **The residency precondition** — refuses to report a null when the state under test could not
   have been resident. *This is the differentiator.*
3. **Block-quantisation conformance** — reuse is granted in whole blocks; a count that is not a
   multiple of the documented block size means the number does not mean what the docs say.
4. ⚠️ **`extra_keys` collision — SHIPPED DISABLED.** This automates an upstream key-derivation
   defect whose fix is **not yet merged**. Publishing a turnkey trigger for an unfixed flaw in a
   project this ecosystem depends on is a 0-day drop, not a contribution. `--include-unfixed`
   enables it and prints why first. Use it against your own staging environment or not at all.

## Live scanning

`kvleak scan` requires a reachable endpoint. Without `--base-url` it exits `2` and reports
nothing — it will not print a result it did not measure:

```console
$ kvleak scan --engine vllm
No --base-url supplied, so nothing was probed and nothing is reported.
Run `kvleak residency ...` first: it needs no endpoint and tells you whether a
scan on this model and cache budget could produce an interpretable result at all.
```

## The fix is not in this package

kvleak tells you whether your stack leaks. **It does not fix it.** Binding a partition key to an
authenticated principal at the admission decision, the ordered composite key, and the separator
hardening are all covered by filed claims and are not distributed here. See
[`CLAIMS-MAP.md`](CLAIMS-MAP.md).

That is stated plainly rather than coyly because you should know what you are getting before you
run it: this is the diagnosis, and it is genuinely and permanently free.

## The commercial edition

kvleak is the **diagnosis** and it is free and permanent. The **fix** — binding a partition key to
an authenticated principal at the admission decision, the ordered composite key, the separator
hardening — is covered by filed claims and licensed separately.

**Finding the leak is free. Closing it is licensed.**

## Honest limits

- **A `CLEAN` result is not a proof of isolation.** It says these probes, under a satisfied
  residency precondition, found no cross-caller reuse. A probe set is not a proof.
- **Residency is computed from a model config and a stated cache budget.** It assumes the budget
  you pass is the budget the engine actually has after weights and activations. Pass the number
  the engine reports, not the card's capacity.
- **The KV arithmetic assumes a standard attention KV cache.** Architectures that compress or
  share cache differently (MLA-style latent caches, for instance) are not modelled, and the tool
  will overstate their footprint.
- **Probe 4 is disabled** and should stay that way until the upstream fix ships.

## Licence

Apache-2.0. See [`LICENSE-TAG`](LICENSE-TAG) for the CLEAN classification and
[`CLAIMS-MAP.md`](CLAIMS-MAP.md) for the claim ranges this scanner approaches and the terminal
step it does not perform.

<!-- HONEST-SCOPE -->
## Honest scope — what a passing run proves, and what it does not

The two halves are inseparable. A tool that states only the first half is marketing.

**It proves:**

- whether a cross-caller probe COULD have detected reuse at all, given the model's KV footprint and your cache budget (the residency precondition)
- whether reuse was observed across callers, with four controls attached
- whether a reuse count is a multiple of the documented block size

**It does NOT prove:**

- that your stack is SAFE. A CLEAN result is bounded by the controls that ran; an INCONCLUSIVE one supports no conclusion in either direction
- that the fix is present — kvleak diagnoses, it never remediates
- anything about an endpoint you did not point it at

Full CLI reference, generated from `--help`: [`docs/CLI.md`](docs/CLI.md)
<!-- /HONEST-SCOPE -->

## Worked example — the refusal that makes a null mean something

Before asking *does my stack leak*, ask whether the probe could have detected a leak at all. If
the victim's cached state is evicted before the attacker probes, a clean result measures two cache
misses.

```console
$ kvleak residency --model qwen2.5-7b --kv-budget-gib 2 --prefixes 60 --tokens 4000
model .............. Qwen2.5-7B-Instruct
  layers ........... 28
  KV heads ......... 4
  KV per token ..... 57,344 bytes

probe set .......... 60 prefixes x 4000 tokens
  requires ......... 12.82 GiB
  available ........ 2.00 GiB
  fits ............. 9 of 60

  INTERPRETABLE: False
  only ~9 of 60 primed prefixes can be resident (12.82 GiB needed, 2.00 GiB available). The
  victim's state would be EVICTED before the attacker probes, so a null measures two cache misses
  and means nothing. REFUSING to report an all-clear.
```

Shrink the probe set until it fits and the same command clears it:

```console
$ kvleak residency --model qwen2.5-7b --kv-budget-gib 20 --prefixes 8 --tokens 800
probe set .......... 8 prefixes x 800 tokens
  requires ......... 0.34 GiB
  available ........ 20.00 GiB
  fits ............. 8 of 8
```

Exit codes: **0** the probe set is interpretable · **1** it is not · **2** the model is unknown —
pass `--config path/to/config.json`. That last one is deliberately *not* 1: not knowing your model
is not the same as telling you your probe is unusable.

## Contributing

Bug reports and pull requests welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

**A false accusation is a defect of equal severity to a missed detection.** If this tool flags something correct, open an issue with the input and the verdict you expected: over-refusal trains people to bypass refusals, which destroys the tool.

Citation metadata is in [CITATION.cff](CITATION.cff).

<!-- PORTFOLIO -->
---

## The rest of the portfolio

25 artifacts, one idea: **a measurement you cannot check is a press release.** Every tool
here reports; none of them gates.

**Tools**

| | |
|---|---|
| [`abstain-bench`](https://github.com/nickharris808/abstain-bench) | how often does a verifier pass input it could not check? |
| [`evidence`](https://github.com/nickharris808/evidence) | run the whole portfolio over your repo — the weakest leg, never the mean |
| [`floorgen`](https://github.com/nickharris808/floorgen) | what must your system remember? an exact lower bound |
| [`formal-proof-mcp`](https://github.com/nickharris808/formal-proof-mcp) | a proof kernel for your coding agent |
| [`gatecount`](https://github.com/nickharris808/gatecount) | exactly how many states does removing this check admit? |
| [`gridlock`](https://github.com/nickharris808/gridlock) | certify a wait-for relation cannot wedge |
| [`honestbench`](https://github.com/nickharris808/honestbench) | measure your CI's escape rate |
| [`kvleak`](https://github.com/nickharris808/kvleak) | cross-tenant leak scanner ← you are here |
| [`kvprobe`](https://github.com/nickharris808/kvprobe) | model-substitution detector with a measured FPR |
| [`preregister`](https://github.com/nickharris808/preregister) | refuses to seal a plan whose conclusion is already fixed |
| [`proof-carrying-ci`](https://github.com/nickharris808/proof-carrying-ci) | the whole portfolio as one CI check, with SARIF |
| [`proof-to-code-drift`](https://github.com/nickharris808/proof-to-code-drift) | fail the build when the proof stops matching |
| [`sf-verify`](https://github.com/nickharris808/sf-verify) | re-derive admission decisions offline |
| [`signoff-cert`](https://github.com/nickharris808/signoff-cert) | certificates that carry their own false-pass bound |
| [`tokencount`](https://github.com/nickharris808/tokencount) | a token count both parties can recompute |

**Benchmarks** — each recomputes one of our own published numbers from its certificate

| | |
|---|---|
| [`illusion-bench`](https://github.com/nickharris808/illusion-bench) | how many broken kernels does your oracle admit? |
| [`kv-reuse-econ-bench`](https://github.com/nickharris808/kv-reuse-econ-bench) | recompute our economics headline |
| [`llm-tenant-isolation-bench`](https://github.com/nickharris808/llm-tenant-isolation-bench) | recompute our isolation figures |

**Datasets**

| | |
|---|---|
| [`abstain-corpus`](https://huggingface.co/datasets/nickh007/abstain-corpus) | 32 inputs a verifier must NOT pass |
| [`kv-reuse-econ-traces`](https://huggingface.co/datasets/nickh007/kv-reuse-econ-traces) | per-workload reuse accounting + the closed form |
| [`kv-tenant-isolation-bench`](https://huggingface.co/datasets/nickh007/kv-tenant-isolation-bench) | isolation observations, uninterpretable rows included |
| [`llm-precision-fingerprints`](https://huggingface.co/datasets/nickh007/llm-precision-fingerprints) | precision-labelled logprobs with a negative control |

**Try it in a browser** — no install, no GPU

| | |
|---|---|
| [`negative-results-atlas`](https://huggingface.co/spaces/nickh007/negative-results-atlas) | ten claims we took back |
| [`tenant-leak-demo`](https://huggingface.co/spaces/nickh007/tenant-leak-demo) | the residency calculator |
| [`wait-for-visualiser`](https://huggingface.co/spaces/nickh007/wait-for-visualiser) | paste a wait-for graph, see the cycle |

### Documentation

Everything above, explained in one place: **<https://nickharris808.github.io/evidence-docs/>** —
the [tutorial](https://nickharris808.github.io/evidence-docs/start/tutorial/),
[what this proves and what it does not](https://nickharris808.github.io/evidence-docs/concepts/what-this-proves/),
and a [CLI reference](https://nickharris808.github.io/evidence-docs/reference/cli/) generated by
running `--help` on every published command.

### The commercial edition

Everything above is **measure-only** and Apache-2.0: it tells you what is true and never acts on
it. The **enforcement** side — binding a partition key at the admission decision, the compiled gate
corpus, and the certificate-*issuing* faucet — is covered by filed patents and licensed separately.

**Reading is free. Enforcing is licensed.**
<!-- /PORTFOLIO -->
