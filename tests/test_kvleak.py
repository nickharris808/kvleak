"""Test suite for kvleak. Every test plants a specific condition and asserts the tool's answer.

Provenance: the 208-token value used throughout is the cross-tenant reuse measured in
`results/data/statefabric/gpu/inengine_weave.json` (registered as `cross_tenant_tokens_unsalted`
in oss/provenance.py). It is a realistic classifier input here, not a claim about any deployment.
"""
from __future__ import annotations

import json

import pytest

from kvleak import (
    ModelKV,
    check_residency,
    evaluate_block_conformance,
    evaluate_reuse,
    explain_gqa_ratio,
    max_interpretable_prefixes,
    tenant_prefix,
)

QWEN = ModelKV("Qwen2.5-1.5B-Instruct", 28, 2, 128, "fp16")     # GQA 12:2
PHI = ModelKV("Phi-3-mini-4k-instruct", 32, 32, 96, "fp16")     # no GQA
GIB = 2 ** 30


# ---------------------------------------------------------------- KV sizing

def test_kv_bytes_per_token_is_2_x_layers_x_kvheads_x_headdim_x_width():
    assert QWEN.bytes_per_token == 2 * 28 * 2 * 128 * 2 == 28672
    assert PHI.bytes_per_token == 2 * 32 * 32 * 96 * 2 == 393216


def test_absence_of_gqa_dominates_kv_footprint():
    """The finding this tool exists to encode: similar-sized models, ~13.7x different KV."""
    ratio = PHI.bytes_per_token / QWEN.bytes_per_token
    assert ratio == pytest.approx(13.71, abs=0.01)
    assert "13.7x more KV per token" in explain_gqa_ratio(QWEN, PHI)


def test_fp8_kv_halves_the_footprint():
    assert ModelKV("x", 32, 32, 96, "fp8").bytes_per_token == PHI.bytes_per_token // 2


def test_unknown_dtype_raises_rather_than_guessing():
    with pytest.raises(ValueError, match="unknown KV dtype"):
        ModelKV("x", 4, 4, 64, "float9").bytes_per_token


def test_config_without_kv_heads_falls_back_to_attention_heads():
    """A model with no GQA omits num_key_value_heads. The fallback must be the WORST case --
    assuming GQA when the field is absent understates footprint by the whole GQA ratio."""
    m = ModelKV.from_config("phi-like", {"num_hidden_layers": 32, "num_attention_heads": 32,
                                         "hidden_size": 3072})
    assert m.num_key_value_heads == 32
    assert m.head_dim == 96
    assert m.bytes_per_token == PHI.bytes_per_token


def test_config_missing_required_fields_raises():
    with pytest.raises(ValueError, match="lacks the fields"):
        ModelKV.from_config("broken", {"num_hidden_layers": 32})


# ---------------------------------------------------------------- the residency precondition

def test_the_known_uninterpretable_case_is_refused():
    """Reproduces the empirical failure: 30x1800 tokens of Phi-3 KV against a 5.6 GiB budget."""
    v = check_residency(PHI, n_prefixes=30, tokens_each=1800, kv_budget_bytes=int(5.6 * GIB))
    assert v.interpretable is False
    assert v.resident_prefixes == 8
    assert v.required_bytes / GIB == pytest.approx(19.78, abs=0.02)
    assert "EVICTED" in v.reason and "REFUSING" in v.reason


def test_the_same_probe_set_is_interpretable_on_a_gqa_model():
    """Identical probe set, identical budget -- the only difference is grouped-query attention."""
    v = check_residency(QWEN, n_prefixes=30, tokens_each=1800, kv_budget_bytes=int(5.6 * GIB))
    assert v.interpretable is True
    assert v.required_bytes / GIB == pytest.approx(1.44, abs=0.02)


def test_a_null_is_interpretable_only_when_the_whole_set_fits():
    budget = PHI.bytes_per_token * 1800 * 8          # exactly 8 prefixes
    assert check_residency(PHI, n_prefixes=8, tokens_each=1800,
                           kv_budget_bytes=budget).interpretable
    assert not check_residency(PHI, n_prefixes=9, tokens_each=1800,
                               kv_budget_bytes=budget).interpretable


def test_safety_margin_demands_real_headroom():
    """A probe set that exactly fills the cache is one eviction from uninterpretable."""
    budget = PHI.bytes_per_token * 1800 * 8
    assert check_residency(PHI, n_prefixes=8, tokens_each=1800,
                           kv_budget_bytes=budget, safety_margin=1.0).interpretable
    assert not check_residency(PHI, n_prefixes=8, tokens_each=1800,
                               kv_budget_bytes=budget, safety_margin=2.0).interpretable


def test_max_interpretable_prefixes_is_the_boundary():
    n = max_interpretable_prefixes(PHI, tokens_each=1800, kv_budget_bytes=int(5.6 * GIB))
    assert n == 8
    assert check_residency(PHI, n_prefixes=n, tokens_each=1800,
                           kv_budget_bytes=int(5.6 * GIB)).interpretable
    assert not check_residency(PHI, n_prefixes=n + 1, tokens_each=1800,
                               kv_budget_bytes=int(5.6 * GIB)).interpretable


@pytest.mark.parametrize("n,t", [(0, 100), (10, 0), (-1, 10)])
def test_degenerate_probe_sets_raise(n, t):
    with pytest.raises(ValueError):
        check_residency(QWEN, n_prefixes=n, tokens_each=t, kv_budget_bytes=GIB)


# ---------------------------------------------------------------- the four controls

def test_a_real_leak_is_reported_as_a_leak():
    r = evaluate_reuse(cross_tenant_cached=208, same_tenant_cached=208,
                       cold_baseline_cached=0, anti_carryover_cached=0, length_matched_cached=0)
    assert r.outcome == "LEAK"
    assert "DIFFERENT identity" in r.detail


def test_isolation_holding_is_reported_clean():
    r = evaluate_reuse(0, 208, 0, 0, 0)
    assert r.outcome == "CLEAN"


def test_caching_off_is_inconclusive_not_clean():
    """The false all-clear this tool refuses to emit: zero cross-tenant reuse because there is
    no reuse at all says nothing whatsoever about isolation."""
    r = evaluate_reuse(0, 0, 0, 0, 0)
    assert r.outcome == "INCONCLUSIVE"
    assert "vacuous" in r.detail


def test_self_inflicted_residency_is_inconclusive():
    """C3: if the harness created the residency, a positive is its own reflection."""
    r = evaluate_reuse(208, 208, 0, 64, 0)
    assert r.outcome == "INCONCLUSIVE"
    assert "self-inflicted" in r.detail


def test_every_result_carries_all_four_controls():
    for args in ((208, 208, 0, 0, 0), (0, 208, 0, 0, 0), (0, 0, 0, 0, 0)):
        c = evaluate_reuse(*args).controls
        assert set(c) == {"C1_cold_baseline", "C2_same_caller_reuse",
                          "C3_anti_carryover", "C4_length_matched"}


# ---------------------------------------------------------------- block conformance

def test_block_conformance_accepts_multiples():
    assert evaluate_block_conformance([16, 32, 208], 16).outcome == "CLEAN"


def test_block_conformance_catches_a_non_multiple():
    r = evaluate_block_conformance([16, 33, 48], 16)
    assert r.outcome == "LEAK"
    assert r.measurements["n_nonconforming"] == 1
    assert "33" in r.detail


def test_block_conformance_with_nothing_to_check_is_skipped_not_clean():
    assert evaluate_block_conformance([], 16).outcome == "SKIPPED"


# ---------------------------------------------------------------- prefixes

def test_two_tenants_never_share_a_prefix():
    """An accidental collision would manufacture the exact leak under test."""
    a = {tenant_prefix("acme", i) for i in range(50)}
    b = {tenant_prefix("evilcorp", i) for i in range(50)}
    assert not (a & b)
    assert len(a) == 50


def test_prefixes_are_deterministic():
    assert tenant_prefix("acme", 3) == tenant_prefix("acme", 3)


# ---------------------------------------------------------------- CLI

def test_cli_selftest_passes():
    from kvleak.cli import main
    assert main(["selftest"]) == 0


def test_cli_residency_exits_nonzero_when_uninterpretable(capsys):
    from kvleak.cli import main
    rc = main(["residency", "--model", "phi-3-mini-4k", "--prefixes", "30",
               "--tokens", "1800", "--kv-budget-gib", "5.6"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "INTERPRETABLE: False" in out
    assert "REFUSING" in out


def test_cli_residency_exits_zero_when_interpretable():
    from kvleak.cli import main
    assert main(["residency", "--model", "qwen2.5-1.5b", "--prefixes", "30",
                 "--tokens", "1800", "--kv-budget-gib", "5.6"]) == 0


def test_cli_residency_json_is_machine_readable(capsys):
    from kvleak.cli import main
    main(["residency", "--model", "phi-3-mini-4k", "--kv-budget-gib", "5.6", "--json"])
    rep = json.loads(capsys.readouterr().out)
    assert rep["interpretable"] is False
    assert rep["resident_prefixes"] == 8


def test_cli_rejects_an_unknown_model_rather_than_guessing(capsys):
    """An unknown model must exit 2, NOT 1.

    In this portfolio 1 means "checked, and the property does not hold" -- for `residency`, that
    the probe set is not interpretable. Not knowing the model is a different thing: nothing was
    checked at all. Exiting 1 told a user their probe was unusable when the tool merely needed a
    --config. This asserts both halves: the code AND that the reason reaches stderr.
    """
    from kvleak.cli import main
    with pytest.raises(SystemExit) as e:
        main(["residency", "--model", "not-a-model", "--kv-budget-gib", "5.6"])
    assert e.value.code == 2, "an unknown model is NOT CHECKED (2), not checked-and-failed (1)"
    err = capsys.readouterr().err
    assert "unknown model" in err
    assert "--config" in err, "the refusal must name the way out"


def test_cli_accepts_an_arbitrary_hf_config(tmp_path):
    from kvleak.cli import main
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"num_hidden_layers": 32, "num_attention_heads": 32,
                               "hidden_size": 3072}))
    assert main(["residency", "--model", "mine", "--config", str(cfg),
                 "--prefixes", "30", "--tokens", "1800", "--kv-budget-gib", "5.6"]) == 1


def test_cli_scan_without_an_endpoint_reports_nothing_rather_than_a_null(capsys):
    """The load-bearing refusal: no endpoint means no measurement, so no all-clear."""
    from kvleak.cli import main
    rc = main(["scan", "--engine", "vllm"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "nothing was probed and nothing is reported" in out


def test_cli_scan_prints_the_unfixed_notice_before_running_probe_4(capsys):
    from kvleak.cli import main
    main(["scan", "--engine", "vllm", "--include-unfixed"])
    assert "NOT yet merged" in capsys.readouterr().out


def test_cli_plan_lists_interpretable_set_sizes(capsys):
    from kvleak.cli import main
    assert main(["plan", "--model", "phi-3-mini-4k", "--kv-budget-gib", "5.6"]) == 0
    assert "max interpretable prefixes" in capsys.readouterr().out
