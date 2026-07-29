"""kvleak.residency — the precondition that stops this tool emitting false all-clears.

THE FAILURE THIS EXISTS TO PREVENT. A cross-tenant prefix-reuse probe works like this: a victim
sends prefixes, then an attacker re-sends them and looks for a speed-up or a cache hit. If the
attacker sees nothing, the natural conclusion is "no leak."

That conclusion is wrong whenever the victim's prefixes were **evicted before the attacker
probed**. The attacker then measured two cache misses and compared them, which is a null that
means nothing at all -- and reporting it as "no leak" is worse than reporting nothing, because it
is a *false all-clear on a system that may well be leaking*.

We learned this the hard way. Replicating our own timing result on a second model produced a
clean null: hit and control medians identical to three decimals. The cause was not isolation. The
model had no grouped-query attention (32 KV heads against 32 attention heads), so it stored ~13.7x
more KV per token than the first model, the victim's primed prefixes needed roughly 19.8 GiB
against ~5.6 GiB of available cache, and most were gone before the attacker ran. The published
attack has a precondition nobody states: **the victim's state must still be resident.**

So `kvleak` computes that precondition FIRST and refuses to report a null when it does not hold.
A refusal to conclude is the honest output; "no leak detected" would be a lie with a number
attached.

Everything here is arithmetic over a model's published config -- no GPU required, which is why it
is also the part of this tool that is fully testable offline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# bytes per element, by KV cache dtype
DTYPE_BYTES = {"fp32": 4, "float32": 4, "fp16": 2, "float16": 2, "bf16": 2, "bfloat16": 2,
               "fp8": 1, "fp8_e5m2": 1, "fp8_e4m3": 1, "int8": 1}


@dataclass
class ModelKV:
    """Just enough of a model config to size its KV cache."""
    name: str
    num_hidden_layers: int
    num_key_value_heads: int
    head_dim: int
    dtype: str = "fp16"

    @classmethod
    def from_config(cls, name: str, cfg: dict) -> "ModelKV":
        """Build from a HuggingFace `config.json`-shaped dict.

        `num_key_value_heads` is the field that matters and the one most often absent: a model
        without grouped-query attention simply omits it, and the correct fallback is
        `num_attention_heads` -- which is the WORST case, not a neutral default. Getting this
        wrong understates KV footprint by the GQA ratio, which is exactly the error that produces
        a false all-clear.
        """
        heads = cfg.get("num_attention_heads")
        kv_heads = cfg.get("num_key_value_heads", heads)
        hidden = cfg.get("hidden_size")
        head_dim = cfg.get("head_dim") or (hidden // heads if hidden and heads else None)
        if not all(isinstance(x, int) and x > 0
                   for x in (cfg.get("num_hidden_layers"), kv_heads, head_dim)):
            raise ValueError(f"config for {name!r} lacks the fields needed to size a KV cache "
                             f"(num_hidden_layers, num_key_value_heads/num_attention_heads, "
                             f"head_dim or hidden_size)")
        return cls(name=name, num_hidden_layers=cfg["num_hidden_layers"],
                   num_key_value_heads=kv_heads, head_dim=head_dim,
                   dtype=cfg.get("torch_dtype", "fp16"))

    @property
    def bytes_per_token(self) -> int:
        """K and V, per layer, per KV head, per element."""
        w = DTYPE_BYTES.get(str(self.dtype).lower())
        if w is None:
            raise ValueError(f"unknown KV dtype {self.dtype!r}; "
                             f"known: {sorted(set(DTYPE_BYTES))}")
        return 2 * self.num_hidden_layers * self.num_key_value_heads * self.head_dim * w

    @property
    def has_gqa(self) -> bool:
        return self.num_key_value_heads > 0


@dataclass
class ResidencyVerdict:
    """Whether a probe's premise can hold. `interpretable` is the whole point."""
    interpretable: bool
    required_bytes: int
    available_bytes: int
    resident_prefixes: int
    requested_prefixes: int
    reason: str

    @property
    def headroom(self) -> float:
        return (self.available_bytes / self.required_bytes) if self.required_bytes else float("inf")

    def as_dict(self) -> dict:
        return {"interpretable": self.interpretable,
                "required_bytes": self.required_bytes,
                "available_bytes": self.available_bytes,
                "required_gib": round(self.required_bytes / 2 ** 30, 3),
                "available_gib": round(self.available_bytes / 2 ** 30, 3),
                "resident_prefixes": self.resident_prefixes,
                "requested_prefixes": self.requested_prefixes,
                "headroom_ratio": round(self.headroom, 4),
                "reason": self.reason}


def check_residency(model: ModelKV, *, n_prefixes: int, tokens_each: int,
                    kv_budget_bytes: int, safety_margin: float = 1.0) -> ResidencyVerdict:
    """Can the victim's whole primed set be resident at once?

    `safety_margin` >= 1.0 demands headroom beyond the bare fit. A probe set that exactly fills
    the cache is one eviction away from being uninterpretable, so the default insists on a true
    fit and callers running against a live, shared engine should raise it.
    """
    if n_prefixes <= 0 or tokens_each <= 0:
        raise ValueError("n_prefixes and tokens_each must be positive")
    per = model.bytes_per_token
    required = per * tokens_each * n_prefixes
    fit = int(kv_budget_bytes // (per * tokens_each)) if per and tokens_each else 0
    need = int(required * safety_margin)

    if kv_budget_bytes >= need:
        return ResidencyVerdict(
            True, required, kv_budget_bytes, min(fit, n_prefixes), n_prefixes,
            f"all {n_prefixes} primed prefixes fit ({required / 2**30:.2f} GiB of "
            f"{kv_budget_bytes / 2**30:.2f} GiB); a null is interpretable")

    return ResidencyVerdict(
        False, required, kv_budget_bytes, max(fit, 0), n_prefixes,
        f"only ~{max(fit, 0)} of {n_prefixes} primed prefixes can be resident "
        f"({required / 2**30:.2f} GiB needed, {kv_budget_bytes / 2**30:.2f} GiB available). "
        f"The victim's state would be EVICTED before the attacker probes, so a null measures "
        f"two cache misses and means nothing. REFUSING to report an all-clear.")


def max_interpretable_prefixes(model: ModelKV, *, tokens_each: int,
                               kv_budget_bytes: int) -> int:
    """The largest probe set whose null would actually be interpretable."""
    per_prefix = model.bytes_per_token * tokens_each
    return int(kv_budget_bytes // per_prefix) if per_prefix else 0


def explain_gqa_ratio(a: ModelKV, b: ModelKV) -> str:
    """Why two models with the same parameter count need wildly different cache budgets."""
    ra, rb = a.bytes_per_token, b.bytes_per_token
    hi, lo = (a, b) if ra >= rb else (b, a)
    return (f"{hi.name} stores {max(ra, rb) / max(1, min(ra, rb)):.1f}x more KV per token than "
            f"{lo.name} ({max(ra, rb):,} vs {min(ra, rb):,} bytes) -- "
            f"{hi.num_key_value_heads} KV heads against {lo.num_key_value_heads}")


__all__ = ["ModelKV", "ResidencyVerdict", "check_residency", "max_interpretable_prefixes",
           "explain_gqa_ratio", "DTYPE_BYTES"]
