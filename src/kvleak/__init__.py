"""kvleak — does your own serving stack leak between tenants?

A black-box scanner for cross-caller prefix reuse on multi-tenant LLM serving stacks, with the
one thing these probes usually lack: a **residency precondition** that refuses to report a null
when the state under test could not have been resident. That refusal is the feature. A tool that
emits "no leak detected" after the victim's prefixes were evicted has told you something false.

MEASURE EDITION. This finds the flaw and reports it. It does not bind a partition key, install a
salt, or gate an admission -- see CLAIMS-MAP.md.
"""
from __future__ import annotations

__version__ = "0.1.0"

from .probes import (
    UNFIXED_NOTICE,
    ProbeResult,
    evaluate_block_conformance,
    evaluate_reuse,
    tenant_prefix,
)
from .residency import (
    DTYPE_BYTES,
    ModelKV,
    ResidencyVerdict,
    check_residency,
    explain_gqa_ratio,
    max_interpretable_prefixes,
)

__all__ = [
    "ModelKV", "ResidencyVerdict", "check_residency", "max_interpretable_prefixes",
    "explain_gqa_ratio", "DTYPE_BYTES",
    "ProbeResult", "tenant_prefix", "evaluate_reuse", "evaluate_block_conformance",
    "UNFIXED_NOTICE", "__version__",
]
