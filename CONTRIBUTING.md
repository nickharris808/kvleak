# Contributing to kvleak

## The rule that matters most

**Never let this tool emit an all-clear it cannot support.**

Every code path that could print "no leak" must first establish that a leak *could have been
observed*. Concretely: the victim's state must have been resident, caching must have been on, and
the anti-carryover control must be clean. If any of those fails the outcome is `INCONCLUSIVE`,
never `CLEAN`.

This is not defensive coding. A security scanner that reports a false all-clear is worse than no
scanner, because the operator now has a document saying they checked.

## The second rule

**This package measures. It never fixes.** A PR that binds a partition key, installs a salt, or
gates an admission changes the artifact's licence classification — see
[`CLAIMS-MAP.md`](CLAIMS-MAP.md). CI enforces it via `check_measure_only.py`.

## Adding a probe

1. State the alternative explanation it rules out. A probe that cannot name what it eliminates is
   a measurement without a hypothesis.
2. Give it a **positive control** and add it to `kvleak selftest`. A probe never shown to fire on
   a real defect is decoration.
3. Add both cases to the test suite: the defect present, and the defect absent. One without the
   other proves nothing.

## Probe 4 stays dark

Do not enable the `extra_keys` collision probe by default, and do not remove the notice, until
the upstream fix has merged and shipped in a release. If you believe that has happened, link the
merged PR and the release tag in your pull request.

## Tests

```bash
pip install -e ".[dev]"
python -m pytest -q          # 33 tests
kvleak selftest              # positive controls for the classifier itself
```
