"""kvleak.probes — the cross-tenant reuse probes, and the controls that make them mean something.

A measurement of cache reuse without controls is unreadable. Four are mandatory here, and each
kills a specific alternative explanation for a positive:

  C1  cold baseline     -- a prefix nobody has sent. Establishes the miss cost. Without it a
                           "fast" response has nothing to be fast relative to.
  C2  same-caller reuse -- the victim re-sends its own prefix. Establishes that reuse is even
                           ON. If this is flat, the engine is not caching and a cross-tenant null
                           is vacuous.
  C3  anti-carryover    -- a distinct prefix from a third identity, interleaved. Catches the case
                           where the harness itself created the residency it then "discovered" --
                           a self-inflicted positive, which is the single most common way this
                           class of measurement goes wrong.
  C4  length-matched    -- a never-sent prefix of the same token count. Separates "this was
                           cached" from "this was shorter".

WHAT THIS TOOL DOES NOT DO. It measures and reports. It does not bind a partition key, install a
salt, or gate an admission -- the fix is not in this package. See CLAIMS-MAP.md.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

# Probe 4 (`extra_keys` collision) is DARK by default. The upstream defect it automates is OPEN
# with its fix unmerged, and shipping a public automated trigger for an unfixed flaw in a project
# we depend on is a 0-day drop, not a disclosure. `--include-unfixed` is the deliberate, explicit
# override, and it prints why before it runs.
UNFIXED_NOTICE = (
    "Probe 4 automates a key-derivation collision whose upstream fix is NOT yet merged. It is "
    "disabled by default. Running it against infrastructure you do not operate may be a hostile "
    "act. Enable it only against your own staging environment, and only if you accept that.")


@dataclass
class ProbeResult:
    name: str
    outcome: str                    # LEAK | CLEAN | INCONCLUSIVE | SKIPPED
    detail: str
    measurements: Dict[str, float] = field(default_factory=dict)
    controls: Dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"probe": self.name, "outcome": self.outcome, "detail": self.detail,
                "measurements": self.measurements, "controls": self.controls}


def tenant_prefix(tenant: str, i: int, tokens: int = 1800) -> str:
    """A deterministic, per-tenant prefix long enough that a prefill is timeable.

    Derived from a hash of (tenant, i) so two tenants NEVER accidentally share a prefix -- an
    accidental collision would manufacture exactly the leak we are testing for.
    """
    seed = hashlib.sha256(f"{tenant}:{i}".encode()).hexdigest()
    unit = f"Confidential runbook {seed} entry {i:05d}. "
    return (unit * max(1, tokens // max(1, len(unit.split())))).strip()


def evaluate_reuse(cross_tenant_cached: int,
                   same_tenant_cached: int,
                   cold_baseline_cached: int,
                   anti_carryover_cached: int,
                   length_matched_cached: int) -> ProbeResult:
    """Classify a data-path result against all four controls.

    The control readings are what turn a number into a finding. Note especially that a
    cross-tenant zero is only CLEAN when C2 is non-zero: if the engine served the victim's own
    repeat from cache and still gave the attacker nothing, isolation held. If C2 is also zero,
    reuse was never on and the result says nothing about isolation at all.
    """
    controls = {
        "C1_cold_baseline": f"{cold_baseline_cached} tokens",
        "C2_same_caller_reuse": f"{same_tenant_cached} tokens",
        "C3_anti_carryover": f"{anti_carryover_cached} tokens",
        "C4_length_matched": f"{length_matched_cached} tokens",
    }

    if anti_carryover_cached > 0:
        return ProbeResult(
            "cross_tenant_prefix_reuse", "INCONCLUSIVE",
            f"anti-carryover control is non-zero ({anti_carryover_cached} tokens): the harness "
            f"created residency it would then have 'discovered'. Any positive here would be "
            f"self-inflicted. Re-run with a cold engine.", controls=controls)

    if same_tenant_cached == 0:
        return ProbeResult(
            "cross_tenant_prefix_reuse", "INCONCLUSIVE",
            "same-caller reuse is zero: prefix caching is not active on this endpoint, so a "
            "cross-tenant zero is vacuous rather than reassuring. Enable caching and re-run.",
            controls=controls)

    if cross_tenant_cached > 0:
        return ProbeResult(
            "cross_tenant_prefix_reuse", "LEAK",
            f"a caller with a DIFFERENT identity was served {cross_tenant_cached} cached tokens "
            f"of another caller's byte-identical prefix, while an unsent length-matched prefix "
            f"got {length_matched_cached}. The cache is keyed on content and not on identity.",
            {"cross_tenant_cached_tokens": cross_tenant_cached,
             "same_tenant_cached_tokens": same_tenant_cached}, controls)

    return ProbeResult(
        "cross_tenant_prefix_reuse", "CLEAN",
        f"cross-tenant reuse is 0 while same-caller reuse is {same_tenant_cached}: reuse is on "
        f"and it is scoped to the caller. This is the result you want.",
        {"cross_tenant_cached_tokens": 0, "same_tenant_cached_tokens": same_tenant_cached},
        controls)


def evaluate_block_conformance(observed: List[int], block_size: int) -> ProbeResult:
    """Probe 3 — does reported reuse land on the engine's documented block quantisation?

    Reuse is granted in whole blocks. A count that is not a multiple of the block size means the
    number is not measuring what the documentation says it measures, and every downstream
    conclusion drawn from it is suspect -- including a reassuring one.
    """
    if not observed:
        return ProbeResult("block_quantisation_conformance", "SKIPPED",
                           "no reuse observed to check quantisation against")
    bad = [n for n in observed if n % block_size != 0]
    if bad:
        return ProbeResult(
            "block_quantisation_conformance", "LEAK",
            f"{len(bad)} of {len(observed)} reuse counts are not multiples of the documented "
            f"block size {block_size}: {bad[:6]}. Reported reuse does not follow the documented "
            f"quantisation, so these counts do not mean what the docs say they mean.",
            {"n_nonconforming": len(bad), "block_size": block_size})
    return ProbeResult(
        "block_quantisation_conformance", "CLEAN",
        f"all {len(observed)} reuse counts are whole multiples of block size {block_size}",
        {"n_checked": len(observed), "block_size": block_size})


__all__ = ["ProbeResult", "tenant_prefix", "evaluate_reuse", "evaluate_block_conformance",
           "UNFIXED_NOTICE"]
