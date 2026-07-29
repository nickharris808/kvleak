# CLAIMS-MAP — kvleak

**Tag: CLEAN. Licence: Apache-2.0.**

This file exists so the CLEAN tag is *auditable* rather than asserted.

## The line

The filed families nearest to this tool are the partition-binding ones. Their independent claims
terminate in:

> *"…**binding** the derived partition key to the authenticated principal at the admission
> decision, and **serving cached state to the request, or refusing to serve it**."*

`kvleak` **finds the flaw**. It sends ordinary requests through a public generate path and
compares what came back. It derives no partition key, binds nothing to any principal, and serves
and refuses nothing.

**The fix is not in this package, and the README says so in plain words rather than coyly.**

## Claims approached, and the step not performed

| Filed claim family | What it recites | What kvleak does instead |
|---|---|---|
| Partition-key binding at admission | deriving a key from an authenticated principal; folding it into the cache lookup; **serving or refusing cached state on the result** | Detects that a stack does *not* do this. Performs no derivation, no binding, and no serve/refuse. |
| Source-tagged ordered composite key + separator hardening | constructing an ordered composite key over tagged sources so distinct sources cannot collide, and **admitting a reuse decision** on it | Probe 3 checks whether observed reuse *conforms to the engine's documented quantisation*. It constructs no key and admits no decision. |
| Conformance methodology | running a declared probe battery with controls and emitting a certificate that **gates** a deployment | Runs the battery and reports. The exit code is a reporting convention; nothing is gated. |

## Why probe 4 is dark

Probe 4 automates an upstream key-derivation collision whose fix is **not yet merged**. It ships
disabled behind `--include-unfixed`, which prints its notice before running.

This is a disclosure decision, not a claims decision. Publishing a turnkey trigger for an unfixed
defect in a project this ecosystem depends on is a 0-day drop rather than a contribution. The
upstream patch goes first.

## Non-claims

- A `CLEAN` result attests that *these probes*, under *this residency precondition*, found no
  cross-caller reuse. It is not a proof of isolation and is not represented as one.
- An `INCONCLUSIVE` result is the honest output when the premise fails. It is deliberately **not**
  reported as a pass, which is the single most important behaviour in this package.
