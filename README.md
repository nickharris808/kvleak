# kvleak

**Your tenant-isolation test passed. Were the victim's prefixes still in the cache when you ran it?**

[![tests](https://github.com/nickharris808/kvleak/actions/workflows/tests.yml/badge.svg)](https://github.com/nickharris808/kvleak/actions/workflows/tests.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](pyproject.toml)

A black-box scanner for cross-caller prefix reuse on multi-tenant LLM serving stacks — with the
thing these probes usually lack: **a residency precondition that refuses to report a null it cannot
interpret.**

## Install

**Not yet on PyPI.** The command below is the one that works today. It installs from this repository, pinned to a tag.

```bash
pip install "git+https://github.com/nickharris808/kvleak@v0.1.0"
```

`pip install kvleak` is the intended command once the name is published. **It 404s today**, which is why it is not the first step above. The tag is pinned rather than `@main` so a reader installs the exact code this README documents.

Zero runtime dependencies. The residency analysis is pure arithmetic; the live probe path uses
`urllib` from the stdlib.

---

## Why this exists — the null that means nothing

A cross-tenant probe works like this: a victim sends prefixes, then an attacker re-sends them and
looks for a cache hit. See nothing, conclude no leak.

**That conclusion is wrong whenever the victim's prefixes were evicted before the attacker probed.**
You then measured two cache misses and compared them. The null is real; it just says nothing about
isolation.

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
  only ~8 of 30 primed prefixes can be resident (19.78 GiB needed, 5.60 GiB available). The victim's state would be EVICTED before the attacker probes, so a null measures two cache misses and means nothing. REFUSING to report an all-clear.

  Largest probe set that WOULD be interpretable here: 8 prefixes.
$ echo $?
1
```

The model has **no grouped-query attention** — 32 KV heads against 32 attention heads — so it stores
13.7× more KV per token than the model we first tested on. Same probe set, same cache budget, on a
GQA model:

```console
$ kvleak residency --model qwen2.5-1.5b --prefixes 30 --tokens 1800 --kv-budget-gib 5.6
model .............. Qwen2.5-1.5B-Instruct
  layers ........... 28
  KV heads ......... 2
  KV per token ..... 28,672 bytes

probe set .......... 30 prefixes x 1800 tokens
  requires ......... 1.44 GiB
  available ........ 5.60 GiB
  fits ............. 30 of 30

  INTERPRETABLE: True
  all 30 primed prefixes fit (1.44 GiB of 5.60 GiB); a null is interpretable
$ echo $?
0
```

**This is a precondition of the published attack that nobody states: the victim's state must still
be resident.** Under cache pressure the attack silently stops working — and a scanner that does not
check will hand you a clean bill of health for a system that is leaking.

## 30-second quickstart

No GPU, no endpoint, no network. Every block below is pasted from a real run.

```bash
# 1. Can a null from this probe set even be interpreted on your model?
kvleak residency --model qwen2.5-7b --kv-budget-gib 2 --prefixes 60 --tokens 4000

# 2. What probe set IS interpretable on your model and cache budget?
kvleak plan --model qwen2.5-7b --kv-budget-gib 12

# 3. Does the tool itself still detect the things it claims to?
kvleak selftest
```

```console
$ kvleak selftest
  ok    leak / clean / caching-off / self-inflicted all classified correctly
  ok    block-quantisation conformance detects a non-multiple
  ok    the known-uninterpretable probe set is refused

selftest passed.
$ echo $?
0
```

```console
$ kvleak plan --model qwen2.5-7b --kv-budget-gib 12
Qwen2.5-7B-Instruct: 57,344 bytes of KV per token

 prefix length   max interpretable prefixes
           256                          877
           512                          438
          1024                          219
          1800                          124
          4096                           54
          8192                           27

A probe set larger than these is not a stronger test -- it is an uninterpretable one.
$ echo $?
0
```

Any HuggingFace `config.json` works via `--config`, so a model kvleak has never heard of is one flag
away. **Run step 1 before you probe anything live**, or you will not know whether a clean scan means
anything.

## Worked example — the refusal that makes a null mean something

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
  only ~9 of 60 primed prefixes can be resident (12.82 GiB needed, 2.00 GiB available). The victim's state would be EVICTED before the attacker probes, so a null measures two cache misses and means nothing. REFUSING to report an all-clear.

  Largest probe set that WOULD be interpretable here: 9 prefixes.
$ echo $?
1
```

Shrink the probe set until it fits and the same command clears it:

```console
$ kvleak residency --model qwen2.5-7b --kv-budget-gib 20 --prefixes 8 --tokens 800
model .............. Qwen2.5-7B-Instruct
  layers ........... 28
  KV heads ......... 4
  KV per token ..... 57,344 bytes

probe set .......... 8 prefixes x 800 tokens
  requires ......... 0.34 GiB
  available ........ 20.00 GiB
  fits ............. 8 of 8

  INTERPRETABLE: True
  all 8 primed prefixes fit (0.34 GiB of 20.00 GiB); a null is interpretable
$ echo $?
0
```

And a model it does not know **abstains rather than guessing** — deliberately exit `2`, not `1`:
not knowing your model is not the same as telling you your probe is unusable.

```console
$ kvleak residency --model llama-4-nonexistent --kv-budget-gib 8
unknown model 'llama-4-nonexistent'. Either pass --config path/to/config.json (any HuggingFace config.json works) or use one of: llama-3.1-8b, phi-3-mini-4k, qwen2.5-1.5b, qwen2.5-7b
$ echo $?
2
```

`--json` gives the same result machine-readably, with the refusal reason attached to the data rather
than only to the prose:

```console
$ kvleak residency --model qwen2.5-7b --kv-budget-gib 2 --prefixes 60 --tokens 4000 --json
{
  "model": "Qwen2.5-7B-Instruct",
  "bytes_per_token": 57344,
  "interpretable": false,
  "required_bytes": 13762560000,
  "available_bytes": 2147483648,
  "required_gib": 12.817,
  "available_gib": 2.0,
  "resident_prefixes": 9,
  "requested_prefixes": 60,
  "headroom_ratio": 0.156,
  "reason": "only ~9 of 60 primed prefixes can be resident (12.82 GiB needed, 2.00 GiB available). The victim's state would be EVICTED before the attacker probes, so a null measures two cache misses and means nothing. REFUSING to report an all-clear."
}
$ echo $?
1
```

## `kvleak scan` — status, stated plainly

**In the build the install line above gives you (`v0.1.0`), the live probe does not run.** Verified
by installing that tag and running it:

```console
$ kvleak scan --engine vllm --base-url http://localhost:8000
kvleak scan requires a reachable serving endpoint.
  engine ....... vllm
  base URL ..... http://localhost:8000

Live probing is not performed in this build: the HTTP probe path is deliberately
not exercised without an endpoint, and this tool refuses to print a result it did
not measure. See README 'Live scanning' for the supported harness.
$ echo $?
2
```

(The "See README 'Live scanning'" it points at is this section — the message predates the rename.)

At `v0.1.0`, `scan` accepts only `--engine`, `--base-url` and `--include-unfixed`, and always exits
`2`. **The classifiers are real and tested** — `selftest` above exercises all four controls — but
nothing feeds them from a live endpoint in that release.

An HTTP driver that closes this gap exists but **is not released**: it is in neither `v0.1.0` nor
any published commit, so `pip install`ing the line at the top of this README will not give it to
you. The output below was captured by running it locally. Treat every `scan` flag other than the
three above as unreleased. Its refusals are the interesting part, and they behave as documented:

```console
$ kvleak scan --engine vllm --base-url http://localhost:8000

ABSTAIN — no --identity-header and no --api-key, so the 'victim' and the 'attacker' would reach the server as the SAME caller.
  A cross-tenant zero measured that way is meaningless: nothing distinguished the two callers. Supply the header your deployment keys identity on.
kvleak scan requires a reachable serving endpoint.
  engine ....... vllm
  base URL ..... http://localhost:8000
$ echo $?
2
```

```console
$ kvleak scan --engine vllm --base-url http://localhost:8000 --identity-header X-Tenant-Id --model foo
ABSTAIN — nothing was measured.
  cannot reach http://localhost:8000/v1/chat/completions: <urlopen error [Errno 61] Connection refused>
kvleak scan requires a reachable serving endpoint.
  engine ....... vllm
  base URL ..... http://localhost:8000
  model ........ foo
  identity via . X-Tenant-Id
  probe tokens . 1800

$ echo $?
2
```

Note what it will not do: it does not fall back to a timing estimate when the server declines to
report `usage.prompt_tokens_details.cached_tokens`. A latency difference is evidence about a cache
only once you have excluded queueing, batching and thermal effects, and a scanner that silently
swapped one for the other would be inventing its own headline.

## The four controls

A cross-tenant measurement without controls is unreadable. Every result carries all four, and each
kills one alternative explanation:

| control | what it rules out |
|---|---|
| **C1** cold baseline | a "fast" response with nothing to be fast relative to |
| **C2** same-caller reuse | *caching was never on*, which makes a cross-tenant zero vacuous |
| **C3** anti-carryover | **the harness created the residency it then "discovered"** |
| **C4** length-matched | "it was cached" confused with "it was shorter" |

C3 is the one that bites hardest. A probe harness that primes and measures in the same engine can
manufacture its own positive, and we have had that happen — which is why a non-zero anti-carryover
reading yields `INCONCLUSIVE` and not a finding.

C2 is the one that produces false comfort. Zero cross-tenant reuse *because there is no reuse at all*
is not isolation:

```python
>>> from kvleak.probes import evaluate_reuse
>>> evaluate_reuse(cross_tenant_cached=0, same_tenant_cached=0, cold_baseline_cached=0,
...                anti_carryover_cached=0, length_matched_cached=0).outcome
'INCONCLUSIVE'
```

### What a real leak looks like

The `208` below is not a made-up number: it is the cross-tenant reuse we measured on a real engine,
recorded in `results/data/statefabric/gpu/inengine_weave.json`
(`apc_probe.evilcorp_cached_tokens_with_apc_on`) and registered in `oss/provenance.py` as
`cross_tenant_tokens_unsalted` — a real-hardware reproduction of the ICML 2502.07776 cross-user cache
leak. It is used here as the **shape of a positive**, not as a claim about your stack.

```python
>>> r = evaluate_reuse(cross_tenant_cached=208, same_tenant_cached=208,
...                    cold_baseline_cached=0, anti_carryover_cached=0, length_matched_cached=0)
>>> r.outcome
'LEAK'
```

`r.detail` then reads: *"a caller with a DIFFERENT identity was served 208 cached tokens of another
caller's byte-identical prefix, while an unsent length-matched prefix got 0. The cache is keyed on
content and not on identity."* `kvleak selftest` asserts exactly this classification, which is why
the selftest is a positive control and not a smoke test.

## Probes, in disclosure-safety order

1. **Cross-caller prefix reuse** — black-box, through public generate paths, with the four controls.
   Targets vLLM and SGLang.
2. **The residency precondition** — refuses to report a null when the state under test could not have
   been resident. *This is the differentiator, and it is the part that runs today with no endpoint.*
3. **Block-quantisation conformance** — reuse is granted in whole blocks; a count that is not a
   multiple of the documented block size means the number does not mean what the docs say.
4. ⚠️ **`extra_keys` collision — SHIPPED DISABLED.** This automates an upstream key-derivation defect
   whose fix is **not yet merged**. Publishing a turnkey trigger for an unfixed flaw in a project this
   ecosystem depends on is a 0-day drop, not a contribution. `--include-unfixed` enables it and
   prints why first. Use it against your own staging environment or not at all.

## CLI reference

```
usage: kvleak [-h] [--version] {residency,plan,scan,selftest} ...

does your serving stack leak between tenants? (measure-only)
```

| command | needs an endpoint? | what it does |
|---|---|---|
| `kvleak residency` | no | can a null from this probe set even be interpreted? |
| `kvleak plan` | no | what probe set IS interpretable on this model and budget |
| `kvleak selftest` | no | positive controls for the tool itself |
| `kvleak scan` | yes | live cross-tenant probe — see the status section above |

### `kvleak residency`

| flag | required | what it does |
|---|---|---|
| `--kv-budget-gib GIB` | **yes** | GiB of KV cache the engine actually has, *after* weights |
| `--model MODEL` | one of these | a known name (`llama-3.1-8b`, `phi-3-mini-4k`, `qwen2.5-1.5b`, `qwen2.5-7b`), or a label for `--config` |
| `--config CONFIG` | one of these | path to any HuggingFace `config.json` |
| `--prefixes PREFIXES` | no | how many prefixes the probe set primes |
| `--tokens TOKENS` | no | tokens per prefix |
| `--safety-margin SAFETY_MARGIN` | no | multiplier on the requirement, default `1.0`. A value above 1.0 demands headroom beyond a bare fit — a probe set that exactly fills the pool is one competing request away from eviction |
| `--json` | no | machine-readable result, refusal reason included |

### `kvleak plan`

Takes `--model` / `--config` and `--kv-budget-gib`, and prints the largest interpretable probe set at
each of six prefix lengths. No `--json`.

### `kvleak scan`

At `v0.1.0`: `--engine {vllm,sglang}`, `--base-url`, `--include-unfixed`. On `main`, additionally
`--model`, `--api-key`, `--identity-header`, `--tokens`, `--timeout`, `--json`.

`--identity-header` is the one that matters: it names the header your deployment keys caller identity
on (e.g. `X-Tenant-Id`). **Without it — or an `--api-key` — victim and attacker reach the server as
the same caller and the probe is meaningless**, so the scan abstains rather than measuring it.

### `kvleak selftest`

No flags. Exits `0` if every positive control fires, `1` if any does not.

### Exit codes

| code | `residency` | `plan` | `scan` | `selftest` |
|---:|---|---|---|---|
| `0` | the probe set **is** interpretable | printed | a result was measured | every control fired |
| `1` | the probe set is **not** interpretable | — | — | a control did not fire |
| `2` | **ABSTAIN** — model unknown, pass `--config` | model unknown | **ABSTAIN** — no endpoint, no identity header, or nothing measured | — |

Note the deliberate split on `residency`: "your probe set is unusable" is `1`, "I do not know your
model" is `2`. Collapsing them would report a verdict about a model the tool never saw.

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

Three more limits, stated because they change how you should read a number:

- **Residency is computed from a model config and a stated cache budget.** It assumes the budget you
  pass is the budget the engine actually has after weights and activations. Pass the number the
  engine reports, not the card's capacity.
- **The KV arithmetic assumes a standard attention KV cache.** Architectures that compress or share
  cache differently (MLA-style latent caches, for instance) are not modelled, and the tool will
  overstate their footprint.
- **Probe 4 is disabled** and should stay that way until the upstream fix ships.

## Troubleshooting

| you see | what it means and how to fix it |
|---|---|
| `ABSTAIN — no --identity-header and no --api-key` (exit 2) | Victim and attacker would reach the server as the same caller, so a cross-tenant zero would mean nothing. Pass the header your gateway keys identity on, e.g. `--identity-header X-Tenant-Id`. This is the scan refusing to run a meaningless experiment, not a bug. |
| `Live probing is not performed in this build` (exit 2) | You are on `v0.1.0`, which ships the classifiers and the residency analysis but no HTTP driver. The three offline subcommands are the whole of that release. |
| `No --base-url supplied, so nothing was probed` (exit 2) | Give it an endpoint — or better, run `kvleak residency` first, which needs no endpoint and tells you whether a scan could produce an interpretable result at all. |
| `cannot reach <url>: <urlopen error ...>` (exit 2) | The endpoint is down, firewalled, or on a different path. Nothing is reported, on purpose. |
| `INTERPRETABLE: False` (exit 1) | Your probe set cannot stay resident. The message prints the largest probe set that *would* be interpretable — shrink `--prefixes` or `--tokens` to it, or raise `--kv-budget-gib` if you understated the engine's real KV pool. |
| `unknown model '<name>'` (exit 2) | Not one of the four built-ins. Pass `--config path/to/config.json`; any HuggingFace config works. |
| `INCONCLUSIVE` rather than `CLEAN` | A control failed. Most often C2: the engine was not caching at all, which makes a cross-tenant zero vacuous. Turn prefix caching on and re-run. |

**Offline behaviour.** `residency`, `plan` and `selftest` make **no network calls at all** and need
no GPU — they are arithmetic over a model config plus a set of classifiers. That is deliberate: the
part of this tool that stops false all-clears is also the part that is fully testable air-gapped, in
CI, before anyone points a probe at a live system. Only `scan` touches the network, and only to the
`--base-url` you name.

## FAQ

**"Isn't a residency check just capacity planning dressed up?"**
It is the difference between a null you can publish and a null you cannot. The published attack has
an unstated precondition — the victim's state must still be resident — and under cache pressure the
attack silently stops working. A scanner that reports "no leak" in that regime is not conservative,
it is wrong. This is the failure we shipped ourselves, on our own second model, which is why it is
the first thing the tool computes.

**"Our probe found a leak. How do we know your harness didn't create it?"**
That is control C3, and it is there because it has bitten this estate before. A probe harness that
primes and measures in the same engine can manufacture its own positive. If the anti-carryover arm
reads non-zero, the run is `INCONCLUSIVE` no matter how dramatic the attacker number looks.

**"We salt the cache per tenant. Are we done?"**
Not necessarily, and this is the sharpest objection in our own audit file. An adversarial run
against this estate's own salting scheme found that a **salt-knowing adversary recovers the victim's
cache hits exactly** — element-for-element identical to the legitimate tenant, on all 12 prefixes and
at all 9 block-boundary offsets. No vulnerability, no timing, no privileged access was needed: the
salt was `sha256(public_namespace | tenant_id)`, and both inputs are public. **A salt is a namespace
separator, not a secret.** Isolation there came entirely from the admission check that refuses a
foreign salt — so if your salt travels client-side, or a tenant can set `cache_salt` directly (the
vLLM API permits it), you should assume it is derivable. The full write-up is held in a private
research repository and is not linkable here; everything it concludes that bears on this tool is
stated in the paragraph above, and `kvleak scan --engine vllm --base-url <your endpoint>` runs the
cross-tenant probe against your own deployment.

**"Could we just turn prefix caching off?"**
You can, and it works, and it is expensive. Measured on this estate's own hardware: disabling prefix
caching cost **+29,280 recomputed prefill tokens, 86.65% of total prefill work** on a multi-turn
workload (`results/data/statefabric/gpu/designaround_price.json`). A per-request *random* salt costs
exactly the same — byte for byte, 33,792 tokens — which is the control worth knowing about: any
salting scheme isolates, but only a salt bound to tenant identity keeps the reuse.

**"A CLEAN verdict means we're safe?"**
No. It means these probes, under a satisfied residency precondition, found no cross-caller reuse. A
probe set is not a proof, and the honest-scope block says so. If you want the cost side of the
trade-off rather than the leak side, that is
[`isolation-tax`](https://github.com/nickharris808/isolation-tax).

**"Why won't you ship probe 4?"**
Because its upstream fix is not merged. Publishing a turnkey trigger for an unfixed flaw in a project
this ecosystem depends on is a 0-day drop, not a disclosure. `--include-unfixed` exists, prints the
reason before it runs, and is your responsibility.

**"Where do your own numbers come from?"**
The `208` above resolves to `results/data/statefabric/gpu/inengine_weave.json`, registered in
`oss/provenance.py` as `cross_tenant_tokens_unsalted`. The portfolio benchmark that recomputes our
published isolation figures from their certificates is
[`llm-tenant-isolation-bench`](https://github.com/nickharris808/llm-tenant-isolation-bench), and the
underlying observations — uninterpretable rows included — are published as
[`kv-tenant-isolation-bench`](https://huggingface.co/datasets/nickh007/kv-tenant-isolation-bench).

## Related tools

| | |
|---|---|
| [`isolation-tax`](https://github.com/nickharris808/isolation-tax) | the other half of the trade-off: what does isolating actually cost you, on your traffic |
| [`llm-tenant-isolation-bench`](https://github.com/nickharris808/llm-tenant-isolation-bench) | recomputes our published isolation figures from their certificates |
| [`abstain-bench`](https://github.com/nickharris808/abstain-bench) | how often does a verifier pass input it could not check? |
| [`tenant-leak-demo`](https://huggingface.co/spaces/nickh007/tenant-leak-demo) | the residency calculator, in a browser — no install, no GPU |

## The fix is not in this package

kvleak tells you whether your stack leaks. **It does not fix it.** Binding a partition key to an
authenticated principal at the admission decision, the ordered composite key, and the separator
hardening are all covered by filed claims and are not distributed here. See
[`CLAIMS-MAP.md`](CLAIMS-MAP.md).

That is stated plainly rather than coyly because you should know what you are getting before you run
it: this is the diagnosis, and it is genuinely and permanently free.

**Finding the leak is free. Closing it is licensed.**

## Licence

Apache-2.0. See [`LICENSE-TAG`](LICENSE-TAG) for the CLEAN classification and
[`CLAIMS-MAP.md`](CLAIMS-MAP.md) for the claim ranges this scanner approaches and the terminal step
it does not perform.

## Contributing

Bug reports and pull requests welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

**A false accusation is a defect of equal severity to a missed detection.** If this tool flags something correct, open an issue with the input and the verdict you expected: over-refusal trains people to bypass refusals, which destroys the tool.

Citation metadata is in [CITATION.cff](CITATION.cff).

<!-- PORTFOLIO -->
---

## The rest of the portfolio

24 artifacts, one idea: **a measurement you cannot check is a press release.** Every tool
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

<!-- BEGIN VERIFY-IN-TEN-MINUTES (generated by oss/tools/gen_readme_standard.py) -->

## Verify this in ten minutes

### 1. Install the version that exists today

```bash
pip install "git+https://github.com/nickharris808/kvleak@v0.1.0"
```

*not on PyPI; the git tag is pinned so a reader installs the exact code this README documents.*

### 2. Run one command

```bash
kvleak selftest
```

Prints every probe run against its control, with no endpoint needed.

### 3. Where the numbers come from

Numbers in this README carry paths like `results/data/...`. **Those receipts live in a private research monorepo and you cannot open them** — they are cited so you can see exactly what was measured and where, not because the link resolves. What is public, and what you can check yourself, is: this package's own tests and `--selftest`; the benchmarks, which recompute the headline numbers from published inputs; and the Hugging Face datasets, whose every row names the certificate it came from. If a number here matters to you and none of those covers it, treat it as unverified.

---

Version 0.1.0 in the source tree · Apache-2.0 · cite via `CITATION.cff` in this repository · this block is generated by `oss/tools/gen_readme_standard.py` from a measurement of PyPI, the git tags and this tree, and `--check` fails if anyone edits it by hand.

<!-- END VERIFY-IN-TEN-MINUTES -->
