from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import algorithms.evaluation.vrc_v8_protocol as protocol
from algorithms.evaluation.vrc_v8_protocol import (
    ArtifactDigest,
    BANDS,
    DEV_SEEDS,
    LAMBDA_GRID,
    SEALED_FINAL_SEEDS,
    S0_VALIDATION_SEEDS,
    S1_TRAIN_SEEDS,
    S1_VALIDATION_SEEDS,
    StageResult,
    calibrate_wrapper,
    canonical_json_hash,
    choose_first_passing_band,
    choose_smallest_passing_lambda,
    evaluate_s0_gate,
    hash_file,
    pooled_m,
    write_stage_artifacts,
)


def _run(seed: int, m: float, departed: float = 10.0) -> dict:
    return {
        "seed": seed,
        "status": "complete",
        "official_metrics": {
            "all_waiting_total_s": float(m) * departed,
            "departed_count": departed,
        },
        # Conflicting duplicates must never be treated as authority.
        "all_waiting_total_s": -999999.0,
        "departed_count": 1.0,
    }


def _runs(values, seeds=DEV_SEEDS, departed=10.0):
    return tuple(_run(seed, value, departed) for seed, value in zip(seeds, values))


def _same_state(**overrides) -> dict:
    evidence = {
        "full_nocollab_flip": 0.02,
        "full_shuffle_action_diff": 0.01,
        "illegal_action_count": 0,
        "nonfinite_count": 0,
        "span_violation_count": 0,
    }
    evidence.update(overrides)
    return evidence


def _verdict(
    *,
    full_values=(99.5,) * 8,
    nocollab_values=(100.0,) * 8,
    shuffle_values=(99.5,) * 8,
    same_state=None,
    seeds=DEV_SEEDS,
):
    shuffle = None if shuffle_values is None else _runs(shuffle_values, seeds)
    return evaluate_s0_gate(
        _runs(full_values, seeds),
        _runs(nocollab_values, seeds),
        shuffle,
        _same_state() if same_state is None else same_state,
    )


def _failed_verdict(*, seeds=DEV_SEEDS):
    return _verdict(
        full_values=(99.6,) * 8,
        nocollab_values=(100.0,) * 8,
        shuffle_values=None,
        seeds=seeds,
    )


def _probe_row(
    seed: int,
    margin: float,
    residual_span: float,
    *,
    candidate_mask=(False, True, False),
    action_masks=(True, True, False),
    valid_action_counts=3,
    probe_row_index=0,
    probe_row_count=1,
) -> dict:
    return {
        "traffic_seed": seed,
        "lambda_scale": 0.20,
        "probe_hash": f"{seed - DEV_SEEDS[0] + 1:064x}",
        "config_hash": "c" * 64,
        "probe_row_index": probe_row_index,
        "probe_row_count": probe_row_count,
        "baseline_logits": (float(margin), 0.0, -1000.0),
        "action_masks": action_masks,
        "valid_action_counts": valid_action_counts,
        "candidate_mask": candidate_mask,
        "gated_collaboration_residual": (float(residual_span), 0.0, 999.0),
    }


def _probe_rows(margins, spans, *, overrides=None):
    overrides = {} if overrides is None else overrides
    rows = []
    for index, (seed, margin, span) in enumerate(zip(DEV_SEEDS, margins, spans)):
        row_overrides = overrides.get(index, {})
        rows.append(_probe_row(seed, margin, span, **row_overrides))
    return tuple(rows)


def _all_lambda_verdicts(*, passing_lambda=None):
    failed = _failed_verdict()
    passed = _verdict()
    return {
        value: passed if value == passing_lambda else failed
        for value in LAMBDA_GRID[1:]
    }


def test_frozen_protocol_constants_are_exact_and_bands_are_immutable():
    assert LAMBDA_GRID == (0.0, 0.025, 0.05, 0.10, 0.20, 0.40, 1.0)
    assert DEV_SEEDS == tuple(range(66501, 66509))
    assert S0_VALIDATION_SEEDS == tuple(range(67501, 67509))
    assert S1_TRAIN_SEEDS == tuple(range(2643, 3043))
    assert S1_VALIDATION_SEEDS == tuple(range(68501, 68509))
    assert SEALED_FINAL_SEEDS == tuple(range(77501, 77511))
    assert BANDS == {
        "Narrow": (0.05, 0.15),
        "Medium": (0.10, 0.25),
        "Wide": (0.20, 0.40),
    }
    with pytest.raises(TypeError):
        BANDS["Narrow"] = (0.0, 1.0)


def test_pooled_m_sums_official_numerators_and_denominators():
    rows = (
        _run(66501, 10.0, departed=1.0),
        _run(66502, 1.0, departed=100.0),
    )
    assert pooled_m(rows) == pytest.approx(110.0 / 101.0)
    assert pooled_m(rows) != pytest.approx((10.0 + 1.0) / 2.0)


@pytest.mark.parametrize(
    "mutation",
    (
        lambda row: row.update(status="partial"),
        lambda row: row["official_metrics"].update(departed_count=0.0),
        lambda row: row["official_metrics"].update(departed_count=-1.0),
        lambda row: row["official_metrics"].update(departed_count=float("nan")),
        lambda row: row["official_metrics"].update(all_waiting_total_s=float("inf")),
        lambda row: row.update(official_metrics={"departed_count": 1.0}),
    ),
)
def test_pooled_m_fails_fast_on_invalid_official_rows(mutation):
    row = _run(66501, 10.0)
    mutation(row)
    with pytest.raises((KeyError, TypeError, ValueError)):
        pooled_m((row,))


@pytest.mark.parametrize("flip", (0.02, 0.15))
def test_s0_gate_inclusive_pass_boundaries_and_auditable_checks(flip):
    verdict = _verdict(same_state=_same_state(full_nocollab_flip=flip))
    assert verdict.status == "PASS"
    assert verdict.passed is True
    checks = {check.name: check for check in verdict.checks}
    assert tuple(checks) == (
        "pooled_ratio",
        "paired_wins",
        "worst_seed_degradation",
        "full_nocollab_flip",
        "full_shuffle_action_diff",
        "online_full_vs_shuffle",
        "illegal_actions",
        "nonfinite_values",
        "span_violations",
    )
    assert checks["pooled_ratio"].measured == pytest.approx(0.995)
    assert checks["pooled_ratio"].comparator == "<="
    assert checks["pooled_ratio"].threshold == 0.995
    assert checks["paired_wins"].evidence == (("total", 8),)
    assert checks["full_nocollab_flip"].threshold == (0.02, 0.15)
    assert all(check.passed and check.evaluated for check in checks.values())
    with pytest.raises(dataclasses.FrozenInstanceError):
        verdict.status = "FAIL"
    with pytest.raises(ValueError):
        dataclasses.replace(verdict, status="FAIL", passed=False)


@pytest.mark.parametrize(
    ("full_values", "nocollab_values", "shuffle_values", "same_state", "failed"),
    (
        ((99.5000000000001,) * 8, (100.0,) * 8, (100.0,) * 8, _same_state(), "pooled_ratio"),
        ((98.0,) * 4 + (100.0,) * 4, (100.0,) * 8, (100.0,) * 8, _same_state(), "paired_wins"),
        ((105.0000000001,) + (98.0,) * 7, (100.0,) * 8, (110.0,) * 8, _same_state(), "worst_seed_degradation"),
        ((99.5,) * 8, (100.0,) * 8, (100.0,) * 8, _same_state(full_nocollab_flip=0.019999), "full_nocollab_flip"),
        ((99.5,) * 8, (100.0,) * 8, (100.0,) * 8, _same_state(full_nocollab_flip=0.150001), "full_nocollab_flip"),
        ((99.5,) * 8, (100.0,) * 8, (100.0,) * 8, _same_state(full_shuffle_action_diff=0.009999), "full_shuffle_action_diff"),
        ((99.5,) * 8, (100.0,) * 8, (99.499,) * 8, _same_state(), "online_full_vs_shuffle"),
        ((99.5,) * 8, (100.0,) * 8, (100.0,) * 8, _same_state(illegal_action_count=1), "illegal_actions"),
        ((99.5,) * 8, (100.0,) * 8, (100.0,) * 8, _same_state(nonfinite_count=1), "nonfinite_values"),
        ((99.5,) * 8, (100.0,) * 8, (100.0,) * 8, _same_state(span_violation_count=1), "span_violations"),
    ),
)
def test_each_s0_subgate_fails_independently(
    full_values, nocollab_values, shuffle_values, same_state, failed
):
    verdict = _verdict(
        full_values=full_values,
        nocollab_values=nocollab_values,
        shuffle_values=shuffle_values,
        same_state=same_state,
    )
    assert verdict.status == "FAIL"
    assert {check.name: check for check in verdict.checks}[failed].passed is False


def test_s0_gate_exactly_five_wins_and_five_percent_degradation_pass():
    full_values = (105.0,) + (95.0,) * 5 + (100.0,) * 2
    verdict = _verdict(
        full_values=full_values,
        nocollab_values=(100.0,) * 8,
        shuffle_values=full_values,
    )
    assert verdict.status == "PASS"
    checks = {check.name: check for check in verdict.checks}
    assert checks["paired_wins"].measured == 5
    assert checks["worst_seed_degradation"].measured == pytest.approx(0.05)


def test_prefilter_failure_can_explicitly_skip_online_shuffle_but_not_omit_check():
    verdict = _failed_verdict()
    assert verdict.status == "FAIL"
    check = {item.name: item for item in verdict.checks}["online_full_vs_shuffle"]
    assert check.evaluated is False
    assert check.passed is False
    assert check.reason == "not_run_after_preliminary_failure"

    invalid = _verdict(shuffle_values=None)
    assert invalid.status == "INVALID"
    assert invalid.passed is False
    assert "online Shuffle" in invalid.reasons[0]


def test_s0_gate_returns_structured_invalid_for_missing_mismatched_or_overflow_inputs():
    incomplete = _same_state()
    del incomplete["nonfinite_count"]
    verdict = _verdict(same_state=incomplete)
    assert verdict.status == "INVALID"
    assert verdict.reasons

    wrong = list(_runs((99.5,) * 8))
    wrong[-1] = _run(99999, 99.5)
    verdict = evaluate_s0_gate(
        wrong,
        _runs((100.0,) * 8),
        _runs((100.0,) * 8),
        _same_state(),
    )
    assert verdict.status == "INVALID"

    overflow = _verdict(
        full_values=(1e300,) * 8,
        nocollab_values=(1e-100,) * 8,
        shuffle_values=(1e300,) * 8,
    )
    assert overflow.status == "INVALID"
    assert all(
        check.measured is None or math.isfinite(float(check.measured))
        for check in overflow.checks
    )


def test_s0_gate_rejects_duplicate_or_sealed_seed_rows_as_invalid():
    duplicate = list(_runs((99.5,) * 8))
    duplicate[-1] = _run(DEV_SEEDS[0], 99.5)
    verdict = evaluate_s0_gate(
        duplicate,
        _runs((100.0,) * 8),
        _runs((100.0,) * 8),
        _same_state(),
    )
    assert verdict.status == "INVALID"
    sealed = evaluate_s0_gate(
        _runs((99.5,) * 8, SEALED_FINAL_SEEDS[:8]),
        _runs((100.0,) * 8, SEALED_FINAL_SEEDS[:8]),
        _runs((100.0,) * 8, SEALED_FINAL_SEEDS[:8]),
        _same_state(),
    )
    assert sealed.status == "INVALID"


def test_choose_smallest_passing_lambda_requires_complete_dev_grid():
    verdicts = _all_lambda_verdicts(passing_lambda=0.05)
    verdicts[0.20] = _verdict(
        full_values=(90.0,) * 8,
        nocollab_values=(100.0,) * 8,
        shuffle_values=(90.0,) * 8,
    )
    assert choose_smallest_passing_lambda(dict(reversed(tuple(verdicts.items())))) == 0.05
    assert choose_smallest_passing_lambda(_all_lambda_verdicts()) is None
    with pytest.raises(ValueError):
        choose_smallest_passing_lambda({0.20: _verdict()})
    with pytest.raises(ValueError):
        choose_smallest_passing_lambda({**verdicts, 0.03: _verdict()})
    validation = _verdict(seeds=S0_VALIDATION_SEEDS)
    wrong_role = dict(verdicts)
    wrong_role[0.05] = validation
    with pytest.raises(ValueError):
        choose_smallest_passing_lambda(wrong_role)


def test_choose_first_passing_band_requires_a_complete_prefix_and_dev_role():
    passed = _verdict()
    failed = _failed_verdict()
    assert choose_first_passing_band({"Narrow": passed}) == "Narrow"
    assert choose_first_passing_band(
        {"Medium": passed, "Narrow": failed}
    ) == "Medium"
    assert choose_first_passing_band({name: failed for name in BANDS}) is None
    with pytest.raises(ValueError):
        choose_first_passing_band({"Medium": passed})
    with pytest.raises(ValueError):
        choose_first_passing_band({"Narrow": passed, "Medium": passed})
    with pytest.raises(ValueError):
        choose_first_passing_band({"ExtraWide": passed})
    with pytest.raises(ValueError):
        choose_first_passing_band(
            {"Narrow": _verdict(seeds=S0_VALIDATION_SEEDS)}
        )


def test_calibration_derives_legal_and_message_predicates_from_raw_probe_fields():
    margins = (0.1, 0.2, 0.5, 1.0, 1.5, 2.5, 4.0, 8.0)
    spans = (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0)
    rows = _probe_rows(
        margins,
        spans,
        overrides={
            2: {"candidate_mask": (False, False, False)},
            6: {"candidate_mask": (False, False, False)},
        },
    )
    calibration = calibrate_wrapper(rows, 0.20)
    expected_quantiles = np.quantile(
        np.asarray(margins),
        [0.05, 0.10, 0.15, 0.20, 0.25, 0.40],
        method="linear",
    )
    eligible_spans = np.asarray([0.2, 0.4, 0.8, 1.0, 1.2, 1.6])
    assert calibration.quantile_method == "linear"
    assert calibration.source_seeds == DEV_SEEDS
    assert calibration.config_hash == "c" * 64
    assert calibration.margin_row_count == 8
    assert calibration.eligible_row_count == 6
    assert np.asarray(calibration.margin_quantiles) == pytest.approx(expected_quantiles)
    assert calibration.c_star == pytest.approx(
        np.quantile(eligible_spans, 0.95, method="linear")
    )
    bands = {band.name: band for band in calibration.bands}
    assert tuple(bands) == ("Narrow", "Medium", "Wide")
    assert (bands["Narrow"].tau1, bands["Narrow"].tau2) == pytest.approx(
        (expected_quantiles[0], expected_quantiles[2])
    )
    assert (bands["Medium"].tau1, bands["Medium"].tau2) == pytest.approx(
        (expected_quantiles[1], expected_quantiles[4])
    )
    assert (bands["Wide"].tau1, bands["Wide"].tau2) == pytest.approx(
        (expected_quantiles[3], expected_quantiles[5])
    )
    assert {band.c_star for band in calibration.bands} == {calibration.c_star}
    with pytest.raises(dataclasses.FrozenInstanceError):
        calibration.c_star = 99.0
    with pytest.raises(ValueError):
        dataclasses.replace(calibration, eligible_row_count=0)
    with pytest.raises(ValueError):
        dataclasses.replace(
            calibration,
            bands=(
                dataclasses.replace(calibration.bands[0], c_star=99.0),
                *calibration.bands[1:],
            ),
        )


def test_calibration_uses_count_conjoined_legal_mask_and_candidate_availability():
    rows = _probe_rows(
        (0.1, 0.2, 0.3, 0.4, 1.0, 1.2, 2.0, 3.0),
        (1.0, 100.0, 100.0, 0.0, 2.0, 100.0, 0.0, 0.0),
        overrides={
            1: {"candidate_mask": (False, False, False)},
            2: {"valid_action_counts": 1},
            5: {"candidate_mask": (False, False, False)},
        },
    )
    calibration = calibrate_wrapper(rows, 0.20)
    assert calibration.margin_row_count == 7
    assert calibration.eligible_row_count == 2
    assert calibration.c_star == pytest.approx(
        np.quantile([0.2, 0.4], 0.95, method="linear")
    )


@pytest.mark.parametrize(
    "rows",
    (
        _probe_rows(
            (0.1, 0.2, 0.3, 0.4, 1.0, 1.2, 2.0, 3.0),
            (0.0,) * 8,
        ),
        _probe_rows(
            (0.1, 0.2, 0.3, 0.4, 1.0, 1.2, 2.0, 3.0),
            (1.0,) * 8,
            overrides={index: {"valid_action_counts": 1} for index in range(8)},
        ),
        _probe_rows((1.0,) * 8, (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0)),
    ),
)
def test_calibration_fails_fast_on_empty_or_degenerate_populations(rows):
    with pytest.raises(ValueError):
        calibrate_wrapper(rows, 0.20)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("baseline_logits", (float("nan"), 0.0, 0.0)),
        ("gated_collaboration_residual", (float("inf"), 0.0, 0.0)),
        ("action_masks", (1, 1, 0)),
        ("candidate_mask", (0, 1, 0)),
        ("valid_action_counts", True),
        ("lambda_scale", 0.10),
        ("probe_hash", "not-a-hash"),
    ),
)
def test_calibration_rejects_nonfinite_coercive_or_unbound_probe_fields(field, value):
    rows = list(
        _probe_rows(
            (0.1, 0.2, 0.3, 0.4, 1.0, 1.2, 2.0, 3.0),
            (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0),
        )
    )
    rows[0][field] = value
    with pytest.raises((TypeError, ValueError)):
        calibrate_wrapper(rows, 0.20)


def test_calibration_requires_complete_dev_seed_and_row_provenance():
    rows = list(
        _probe_rows(
            (0.1, 0.2, 0.3, 0.4, 1.0, 1.2, 2.0, 3.0),
            (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0),
        )
    )
    rows[0]["traffic_seed"] = S0_VALIDATION_SEEDS[0]
    with pytest.raises(ValueError):
        calibrate_wrapper(rows, 0.20)

    rows = list(_probe_rows((0.1,) * 7 + (2.0,), (1.0,) * 8))
    rows[0]["probe_row_count"] = 2
    with pytest.raises(ValueError):
        calibrate_wrapper(rows, 0.20)


def test_hashes_are_sha256_canonical_and_sealed_paths_are_rejected(tmp_path):
    path = tmp_path / "payload.bin"
    path.write_bytes(b"abc")
    assert hash_file(path) == hashlib.sha256(b"abc").hexdigest()
    assert canonical_json_hash({"b": [2, 1], "a": "值"}) == canonical_json_hash(
        {"a": "值", "b": [2, 1]}
    )
    assert canonical_json_hash({"a": [1, 2]}) != canonical_json_hash(
        {"a": [2, 1]}
    )
    with pytest.raises((TypeError, ValueError)):
        canonical_json_hash({"bad": float("nan")})
    with pytest.raises((TypeError, ValueError)):
        canonical_json_hash({"bad": {1, 2}})

    synthetic_sealed = tmp_path / "result_77501.json"
    synthetic_sealed.write_text("synthetic", encoding="utf-8")
    with pytest.raises(ValueError):
        hash_file(synthetic_sealed)


def test_sealed_path_guard_rejects_input_and_output_symlink_aliases(tmp_path):
    sealed_input = tmp_path / "probe_77501.bin"
    sealed_input.write_bytes(b"synthetic sealed input")
    input_alias = tmp_path / "probe.bin"
    input_alias.symlink_to(sealed_input)
    with pytest.raises(ValueError):
        hash_file(input_alias)

    result = _stage_result(tmp_path / "inputs")
    sealed_output = tmp_path / "stage_77501"
    sealed_output.mkdir()
    output_alias = tmp_path / "S0-D"
    output_alias.symlink_to(sealed_output, target_is_directory=True)
    with pytest.raises(ValueError):
        write_stage_artifacts(output_alias, result)
    assert tuple(sealed_output.iterdir()) == ()


def test_protocol_file_io_resists_symlink_swaps_after_preflight(monkeypatch, tmp_path):
    safe_input = tmp_path / "safe.bin"
    safe_input.write_bytes(b"safe")
    sealed_input = tmp_path / "synthetic_77501.bin"
    sealed_input.write_bytes(b"sealed")
    original_guard = protocol._reject_symlink_components
    swapped_input = False

    def swap_input_after_guard(path):
        nonlocal swapped_input
        absolute = original_guard(path)
        if Path(path) == safe_input and not swapped_input:
            safe_input.unlink()
            safe_input.symlink_to(sealed_input)
            swapped_input = True
        return absolute

    monkeypatch.setattr(protocol, "_reject_symlink_components", swap_input_after_guard)
    with pytest.raises(ValueError):
        hash_file(safe_input)

    monkeypatch.setattr(protocol, "_reject_symlink_components", original_guard)
    result = _stage_result(tmp_path / "inputs")
    stage_dir = tmp_path / "race-stage"
    sealed_output = tmp_path / "synthetic_77501_stage"
    sealed_output.mkdir()
    child_checks = 0

    def swap_output_after_last_guard(path):
        nonlocal child_checks
        absolute = original_guard(path)
        if Path(path).parent == stage_dir:
            child_checks += 1
            if child_checks == 5:
                if stage_dir.exists():
                    stage_dir.rmdir()
                stage_dir.symlink_to(sealed_output, target_is_directory=True)
        return absolute

    monkeypatch.setattr(protocol, "_reject_symlink_components", swap_output_after_last_guard)
    with pytest.raises(ValueError):
        write_stage_artifacts(stage_dir, result)
    assert tuple(sealed_output.iterdir()) == ()


def _input_artifacts(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    artifacts = []
    for role, payload in (
        ("checkpoint", b"checkpoint"),
        ("code", b"code manifest"),
        ("probe", b"probe manifest"),
        ("v2x_config", b"v2x config"),
    ):
        path = tmp_path / f"{role}.bin"
        path.write_bytes(payload)
        artifacts.append(ArtifactDigest(role, str(path), hash_file(path)))
    return tuple(artifacts)


def _stage_result(tmp_path: Path) -> StageResult:
    verdicts = _all_lambda_verdicts(passing_lambda=0.025)
    return StageResult(
        stage="S0-D",
        status="PASS",
        verdicts=tuple((str(value), verdict) for value, verdict in verdicts.items()),
        selection={"lambda_star": 0.025, "reason": "smallest passing lambda"},
        calibration=None,
        manifest={"commands": [["python", "evaluate.py"]]},
        config={"quantile_method": "linear", "lambda_grid": list(LAMBDA_GRID)},
        input_artifacts=_input_artifacts(tmp_path),
        evidence={"lambda_grid_complete": True},
    )


def test_stage_payload_parser_roundtrips_only_writer_producible_typed_results(
    tmp_path,
):
    result = _stage_result(tmp_path / "inputs")
    payloads = protocol._stage_payloads(result)

    parsed = protocol.stage_result_from_payloads(
        payloads["manifest"],
        payloads["report"],
        payloads["config"],
    )
    assert protocol._stage_payloads(parsed) == payloads

    forged = json.loads(json.dumps(payloads["report"]))
    forged.pop("verdicts")
    with pytest.raises(ValueError, match="schema|verdict|typed"):
        protocol.stage_result_from_payloads(
            payloads["manifest"], forged, payloads["config"]
        )


def test_write_stage_artifacts_derives_auditable_json_hashes_and_markdown(tmp_path):
    result = _stage_result(tmp_path)
    stage_dir = tmp_path / "S0-D"
    write_stage_artifacts(stage_dir, result)
    assert {path.name for path in stage_dir.iterdir()} == {
        "manifest.json",
        "report.json",
        "report.md",
        "config.json",
        "hashes.json",
    }
    report = json.loads((stage_dir / "report.json").read_text())
    hashes = json.loads((stage_dir / "hashes.json").read_text())
    assert report["schema"] == "vrc_v8_stage_report_v1"
    assert len(report["verdicts"]) == len(LAMBDA_GRID) - 1
    assert report["input_artifacts"] == json.loads(
        (stage_dir / "manifest.json").read_text()
    )["input_artifacts"]
    assert hashes["report_hash"] == canonical_json_hash(report)
    assert hashes["checkpoint_hash"] == report["input_hashes"]["checkpoint_hash"]
    seed_row = report["verdicts"][0]["verdict"]["per_seed"][0]
    assert seed_row["full_waiting_total_s"] == 995.0
    assert seed_row["full_departed_count"] == 10.0
    assert seed_row["nocollab_waiting_total_s"] == 1000.0
    assert seed_row["nocollab_departed_count"] == 10.0
    assert seed_row["shuffle_waiting_total_s"] == 995.0
    assert seed_row["shuffle_departed_count"] == 10.0
    markdown = (stage_dir / "report.md").read_text(encoding="utf-8")
    assert "Verdict: PASS" in markdown
    assert '"name": "pooled_ratio"' in markdown
    write_stage_artifacts(stage_dir, result)


def test_stage_result_rejects_missing_or_contradictory_pass_evidence(tmp_path):
    result = _stage_result(tmp_path)
    with pytest.raises(ValueError):
        dataclasses.replace(result, stage="S0-X")
    with pytest.raises(ValueError):
        dataclasses.replace(result, status="PASSS")
    with pytest.raises(ValueError):
        dataclasses.replace(result, verdicts=())
    with pytest.raises(ValueError):
        dataclasses.replace(result, selection={"lambda_star": 0.20})
    with pytest.raises(ValueError):
        dataclasses.replace(result, selection={"lambda_star": 0.025})
    duplicate = result.input_artifacts + (
        ArtifactDigest("checkpoint", result.input_artifacts[0].path, "0" * 64),
    )
    with pytest.raises(ValueError):
        dataclasses.replace(result, input_artifacts=duplicate)

    mutable_verdicts = list(result.verdicts)
    mutable_artifacts = list(result.input_artifacts)
    copied = dataclasses.replace(
        result,
        verdicts=mutable_verdicts,
        input_artifacts=mutable_artifacts,
    )
    mutable_verdicts.clear()
    mutable_artifacts.clear()
    assert isinstance(copied.verdicts, tuple)
    assert isinstance(copied.input_artifacts, tuple)
    assert len(copied.verdicts) == len(LAMBDA_GRID) - 1
    assert len(copied.input_artifacts) == 4

    with pytest.raises(ValueError):
        dataclasses.replace(
            result,
            status="FAIL",
            verdicts=(),
            selection={},
            evidence={},
        )


def test_s0d_terminal_fail_requires_complete_negative_lambda_grid(tmp_path):
    result = _stage_result(tmp_path)
    with pytest.raises(ValueError):
        dataclasses.replace(
            result,
            status="FAIL",
            verdicts=(("0.025", _failed_verdict()),),
            selection={"reason": "no passing lambda"},
            evidence={"lambda_grid_complete": False},
        )
    complete_s0d = dataclasses.replace(
        result,
        status="FAIL",
        verdicts=tuple(
            (str(value), _failed_verdict()) for value in LAMBDA_GRID[1:]
        ),
        selection={"reason": "no passing lambda"},
        evidence={"lambda_grid_complete": True},
    )
    assert complete_s0d.status == "FAIL"


def test_s0w_terminal_fail_requires_complete_negative_band_scan(tmp_path):
    result = _stage_result(tmp_path)
    calibration = calibrate_wrapper(
        _probe_rows(
            (0.1, 0.2, 0.5, 1.0, 1.5, 2.5, 4.0, 8.0),
            (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0),
        ),
        0.20,
    )
    s0w_fields = {
        "stage": "S0-W",
        "status": "FAIL",
        "selection": {"lambda_star": 0.20, "reason": "no passing band"},
        "calibration": calibration,
        "manifest": {"commands": [["python", "evaluate.py"]]},
        "config": {"quantile_method": "linear", "lambda_grid": list(LAMBDA_GRID)},
        "input_artifacts": result.input_artifacts,
        "evidence": {"band_prefix_complete": True},
    }
    with pytest.raises(ValueError):
        StageResult(verdicts=(("Narrow", _failed_verdict()),), **s0w_fields)
    complete_s0w = StageResult(
        verdicts=tuple((name, _failed_verdict()) for name in BANDS),
        **s0w_fields,
    )
    assert complete_s0w.status == "FAIL"


def test_nested_verdict_and_calibration_sequences_are_defensively_copied():
    verdict = _verdict()
    mutable_checks = list(verdict.checks)
    copied_verdict = dataclasses.replace(verdict, checks=mutable_checks)
    mutable_checks.clear()
    assert isinstance(copied_verdict.checks, tuple)
    assert len(copied_verdict.checks) == 9

    mutable_evidence = [["total", 8]]
    copied_check = dataclasses.replace(
        verdict.checks[1], evidence=mutable_evidence
    )
    mutable_evidence[0].clear()
    mutable_evidence.clear()
    assert copied_check.evidence == (("total", 8),)

    calibration = calibrate_wrapper(
        _probe_rows(
            (0.1, 0.2, 0.5, 1.0, 1.5, 2.5, 4.0, 8.0),
            (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0),
        ),
        0.20,
    )
    mutable_bands = list(calibration.bands)
    mutable_hashes = [list(item) for item in calibration.probe_hashes]
    copied_calibration = dataclasses.replace(
        calibration,
        bands=mutable_bands,
        probe_hashes=mutable_hashes,
    )
    mutable_bands.clear()
    mutable_hashes[0].clear()
    mutable_hashes.clear()
    assert isinstance(copied_calibration.bands, tuple)
    assert copied_calibration.bands == calibration.bands
    assert copied_calibration.probe_hashes == calibration.probe_hashes


def test_calibration_seed_provenance_requires_exact_integers():
    calibration = calibrate_wrapper(
        _probe_rows(
            (0.1, 0.2, 0.5, 1.0, 1.5, 2.5, 4.0, 8.0),
            (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0),
        ),
        0.20,
    )
    with pytest.raises(TypeError):
        dataclasses.replace(
            calibration,
            source_seeds=tuple(float(seed) for seed in calibration.source_seeds),
        )
    with pytest.raises(TypeError):
        dataclasses.replace(
            calibration,
            probe_hashes=tuple(
                (float(seed), digest) for seed, digest in calibration.probe_hashes
            ),
        )


def test_protocol_records_reject_bool_and_mutable_lookalikes(tmp_path):
    result = _stage_result(tmp_path)
    verdict = result.verdicts[0][1]
    with pytest.raises(TypeError):
        dataclasses.replace(verdict, passed=1)
    with pytest.raises(TypeError):
        dataclasses.replace(verdict.checks[0], passed=1)
    with pytest.raises(TypeError):
        dataclasses.replace(verdict.checks[0], evaluated=1)
    with pytest.raises(TypeError):
        dataclasses.replace(verdict.per_seed[0], full_win=1)

    check_lookalikes = tuple(
        SimpleNamespace(**dataclasses.asdict(check)) for check in verdict.checks
    )
    with pytest.raises(TypeError):
        dataclasses.replace(verdict, checks=check_lookalikes)
    metric_lookalikes = tuple(
        SimpleNamespace(**dataclasses.asdict(metric)) for metric in verdict.per_seed
    )
    with pytest.raises(TypeError):
        dataclasses.replace(verdict, per_seed=metric_lookalikes)

    artifact_lookalikes = tuple(
        SimpleNamespace(role=item.role, path=item.path, sha256=item.sha256)
        for item in result.input_artifacts
    )
    with pytest.raises(TypeError):
        dataclasses.replace(result, input_artifacts=artifact_lookalikes)

    calibration = calibrate_wrapper(
        _probe_rows(
            (0.1, 0.2, 0.5, 1.0, 1.5, 2.5, 4.0, 8.0),
            (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0),
        ),
        0.20,
    )
    with pytest.raises(TypeError):
        dataclasses.replace(calibration, lambda_star=True)
    band_lookalikes = tuple(
        SimpleNamespace(
            name=band.name,
            tau1=band.tau1,
            tau2=band.tau2,
            c_star=band.c_star,
        )
        for band in calibration.bands
    )
    with pytest.raises(TypeError):
        dataclasses.replace(calibration, bands=band_lookalikes)

    with pytest.raises(TypeError):
        StageResult(
            stage="S0-W",
            status="PASS",
            verdicts=(("Narrow", _verdict()),),
            selection={
                "lambda_star": 0.20,
                "band": "Narrow",
                "reason": "first passing band",
            },
            calibration=SimpleNamespace(lambda_star=0.20),
            manifest={"commands": [["python", "evaluate.py"]]},
            config={"quantile_method": "linear", "lambda_grid": list(LAMBDA_GRID)},
            input_artifacts=result.input_artifacts,
            evidence={"band_prefix_complete": True},
        )


def test_gate_records_reject_semantically_forged_pass_evidence():
    verdict = _verdict()
    pooled_check = verdict.checks[0]
    with pytest.raises(ValueError):
        dataclasses.replace(pooled_check, measured=1.0, passed=True)

    for forged_check in (
        dataclasses.replace(pooled_check, measured=0.5),
        dataclasses.replace(pooled_check, threshold=2.0),
        dataclasses.replace(pooled_check, comparator=">="),
    ):
        checks = list(verdict.checks)
        checks[0] = forged_check
        with pytest.raises(ValueError):
            dataclasses.replace(verdict, checks=checks)

    metric = verdict.per_seed[0]
    with pytest.raises(ValueError):
        dataclasses.replace(metric, full_win=not metric.full_win)
    with pytest.raises(ValueError):
        dataclasses.replace(metric, degradation=0.0)
    with pytest.raises(ValueError):
        dataclasses.replace(verdict, full_pooled_m=1.0)

    with pytest.raises(ValueError):
        dataclasses.replace(
            verdict.checks[1], evidence=(("total", 8), ("total", 9))
        )
    checks = list(verdict.checks)
    checks[5] = dataclasses.replace(
        checks[5], evidence=(("shuffle_pooled_m", 1.0),)
    )
    with pytest.raises(ValueError):
        dataclasses.replace(verdict, checks=checks)


def test_gate_verdict_rejects_pooled_metrics_not_derived_from_per_seed_rows():
    failed = _verdict(
        full_values=(99.6,) * 8,
        nocollab_values=(100.0,) * 8,
        shuffle_values=(100.0,) * 8,
    )
    assert failed.status == "FAIL"
    checks = list(failed.checks)
    checks[0] = dataclasses.replace(checks[0], measured=0.99, passed=True)
    checks[5] = dataclasses.replace(checks[5], measured=99.0)
    with pytest.raises(ValueError, match="per-seed"):
        dataclasses.replace(
            failed,
            status="PASS",
            passed=True,
            checks=checks,
            full_pooled_m=99.0,
            full_waiting_sum=990.0,
            full_departed_sum=10.0,
        )


def test_gate_verdict_rejects_tolerance_sized_threshold_crossing():
    failed = _verdict(
        full_values=(99.50000000005,) * 8,
        nocollab_values=(100.0,) * 8,
        shuffle_values=(100.0,) * 8,
    )
    assert failed.status == "FAIL"
    checks = list(failed.checks)
    checks[0] = dataclasses.replace(checks[0], measured=0.995, passed=True)
    checks[5] = dataclasses.replace(checks[5], measured=99.5)
    with pytest.raises(ValueError):
        dataclasses.replace(
            failed,
            status="PASS",
            passed=True,
            checks=checks,
            full_pooled_m=99.5,
            full_waiting_sum=7960.0,
        )


def test_seed_metric_derives_win_truth_from_exact_raw_components():
    metric = _verdict().per_seed[0]
    almost_tied = 99.99999999995
    with pytest.raises(ValueError):
        dataclasses.replace(
            metric,
            full_m=almost_tied,
            full_waiting_total_s=1000.0,
            full_win=True,
            degradation=(almost_tied - 100.0) / 100.0,
        )


def test_nested_protocol_records_must_be_exact_types(tmp_path):
    result = _stage_result(tmp_path)

    class GateCheckSubclass(protocol.GateCheck):
        pass

    base_check = result.verdicts[0][1].checks[0]
    subclassed_check = GateCheckSubclass(
        base_check.name,
        base_check.measured,
        base_check.comparator,
        base_check.threshold,
        base_check.passed,
        base_check.evidence,
        base_check.evaluated,
        base_check.reason,
    )
    with pytest.raises(TypeError):
        dataclasses.replace(
            result.verdicts[0][1],
            checks=(subclassed_check, *result.verdicts[0][1].checks[1:]),
        )

    class ArtifactDigestSubclass(ArtifactDigest):
        pass

    base_artifact = result.input_artifacts[0]
    subclassed_artifact = ArtifactDigestSubclass(
        base_artifact.role,
        base_artifact.path,
        base_artifact.sha256,
    )
    with pytest.raises(TypeError):
        dataclasses.replace(
            result,
            input_artifacts=(subclassed_artifact, *result.input_artifacts[1:]),
        )


def test_writer_revalidates_frozen_records_before_publication(tmp_path):
    result = _stage_result(tmp_path / "inputs")
    object.__setattr__(result.verdicts[0][1].checks[0], "measured", 1.0)
    with pytest.raises(ValueError):
        write_stage_artifacts(tmp_path / "stage", result)
    assert not (tmp_path / "stage" / "report.json").exists()


def test_writer_publishes_one_snapshot_when_live_records_change_during_io(
    tmp_path, monkeypatch
):
    result = _stage_result(tmp_path / "inputs")
    original_hash_file = protocol.hash_file
    check = result.verdicts[0][1].checks[0]
    artifact = result.input_artifacts[0]
    mutated = False

    def mutate_after_snapshot(path):
        nonlocal mutated
        if not mutated:
            mutated = True
            object.__setattr__(check, "measured", 1.0)
            object.__setattr__(artifact, "role", "forged")
        return original_hash_file(path)

    monkeypatch.setattr(protocol, "hash_file", mutate_after_snapshot)
    stage_dir = tmp_path / "stage"
    write_stage_artifacts(stage_dir, result)

    report = json.loads((stage_dir / "report.json").read_text())
    published_check = report["verdicts"][0]["verdict"]["checks"][0]
    assert published_check["measured"] == 0.995
    assert published_check["passed"] is True
    assert report["input_artifacts"][0]["role"] == "checkpoint"
    assert "checkpoint_hash" in report["input_hashes"]
    assert "forged_hash" not in report["input_hashes"]


def test_protocol_records_snapshot_numeric_subclasses_as_plain_scalars():
    flip = {"on": False}

    class FlippingFloat(float):
        def __float__(self):
            return 1.0 if flip["on"] else super().__float__()

    verdict = _verdict()
    base_check = verdict.checks[0]
    check = dataclasses.replace(
        base_check,
        measured=FlippingFloat(base_check.measured),
        evidence=(("audit", FlippingFloat(base_check.measured)),),
    )
    base_metric = verdict.per_seed[0]
    metric = dataclasses.replace(
        base_metric,
        full_m=FlippingFloat(base_metric.full_m),
        full_waiting_total_s=FlippingFloat(base_metric.full_waiting_total_s),
    )
    copied_verdict = dataclasses.replace(
        verdict,
        full_pooled_m=FlippingFloat(verdict.full_pooled_m),
        full_waiting_sum=FlippingFloat(verdict.full_waiting_sum),
    )

    flip["on"] = True
    assert type(check.measured) is float and check.measured == base_check.measured
    assert type(check.evidence[0][1]) is float
    assert check.evidence[0][1] == base_check.measured
    assert type(metric.full_m) is float and metric.full_m == base_metric.full_m
    assert type(metric.full_waiting_total_s) is float
    assert metric.full_waiting_total_s == base_metric.full_waiting_total_s
    assert type(copied_verdict.full_pooled_m) is float
    assert copied_verdict.full_pooled_m == verdict.full_pooled_m
    assert type(copied_verdict.full_waiting_sum) is float
    assert copied_verdict.full_waiting_sum == verdict.full_waiting_sum


def _s1_implementation_failures(**overrides):
    values = {name: 0 for name in protocol.S1_IMPLEMENTATION_CHECKS}
    values.update(overrides)
    return values


def _s1_mechanism(*, seeds=DEV_SEEDS, full=98.0, passed=True):
    if passed:
        return _verdict(
            full_values=(full,) * 8,
            nocollab_values=(100.0,) * 8,
            shuffle_values=(full,) * 8,
            seeds=seeds,
        )
    return _failed_verdict(seeds=seeds)


def test_s1_adjudication_distinguishes_implementation_mechanism_valid_and_go():
    dev_mechanism = _s1_mechanism()
    dev_pass = protocol.adjudicate_s1(
        mechanism=dev_mechanism,
        s1_full_rows=_runs((98.0,) * 8),
        s0_wrapper_rows=_runs((98.0,) * 8),
        senior_rows=_runs((120.0,) * 8),
        implementation_failures=_s1_implementation_failures(),
        seed_role="dev",
    )
    assert (dev_pass.status, dev_pass.subtype) == ("PASS", None)
    assert dev_pass.pooled_ratio == 1.0

    implementation_fail = protocol.adjudicate_s1(
        mechanism=dev_mechanism,
        s1_full_rows=_runs((98.0,) * 8),
        s0_wrapper_rows=_runs((98.0,) * 8),
        senior_rows=_runs((1.0,) * 8),
        implementation_failures=_s1_implementation_failures(
            baseline_drift_count=1
        ),
        seed_role="dev",
    )
    assert (implementation_fail.status, implementation_fail.subtype) == (
        "S1_FAIL",
        "IMPLEMENTATION_INVALID",
    )

    malformed_mechanism = protocol.evaluate_s0_gate([], [], None, {})
    assert malformed_mechanism.status == "INVALID"
    malformed = protocol.adjudicate_s1(
        mechanism=malformed_mechanism,
        s1_full_rows=_runs((98.0,) * 8),
        s0_wrapper_rows=_runs((98.0,) * 8),
        senior_rows=_runs((120.0,) * 8),
        implementation_failures=_s1_implementation_failures(),
        seed_role="dev",
    )
    assert (malformed.status, malformed.subtype) == (
        "S1_FAIL",
        "IMPLEMENTATION_INVALID",
    )

    mechanism_fail = protocol.adjudicate_s1(
        mechanism=_s1_mechanism(passed=False),
        s1_full_rows=_runs((99.6,) * 8),
        s0_wrapper_rows=_runs((98.0,) * 8),
        senior_rows=_runs((500.0,) * 8),
        implementation_failures=_s1_implementation_failures(),
        seed_role="dev",
    )
    assert (mechanism_fail.status, mechanism_fail.subtype) == (
        "S1_FAIL",
        "MECHANISM_FAIL",
    )

    validation_mechanism = _s1_mechanism(
        seeds=S1_VALIDATION_SEEDS
    )
    valid = protocol.adjudicate_s1(
        mechanism=validation_mechanism,
        s1_full_rows=_runs((98.0,) * 8, S1_VALIDATION_SEEDS),
        s0_wrapper_rows=_runs((98.0,) * 8, S1_VALIDATION_SEEDS),
        senior_rows=_runs((1.0,) * 8, S1_VALIDATION_SEEDS),
        implementation_failures=_s1_implementation_failures(),
        seed_role="s1_validation",
    )
    assert (valid.status, valid.subtype) == ("S1_VALID", None)

    go = protocol.adjudicate_s1(
        mechanism=validation_mechanism,
        s1_full_rows=_runs((98.0,) * 8, S1_VALIDATION_SEEDS),
        s0_wrapper_rows=_runs((99.0,) * 8, S1_VALIDATION_SEEDS),
        senior_rows=_runs((1000.0,) * 8, S1_VALIDATION_SEEDS),
        implementation_failures=_s1_implementation_failures(),
        seed_role="s1_validation",
    )
    assert (go.status, go.subtype) == ("GO_V8L", None)
    assert go.paired_wins == 8


def test_s1_senior_is_report_only_and_mechanism_rows_are_bound():
    mechanism = _s1_mechanism(seeds=S1_VALIDATION_SEEDS)
    kwargs = {
        "mechanism": mechanism,
        "s1_full_rows": _runs((98.0,) * 8, S1_VALIDATION_SEEDS),
        "s0_wrapper_rows": _runs((99.0,) * 8, S1_VALIDATION_SEEDS),
        "implementation_failures": _s1_implementation_failures(),
        "seed_role": "s1_validation",
    }
    low = protocol.adjudicate_s1(
        **kwargs,
        senior_rows=_runs((1.0,) * 8, S1_VALIDATION_SEEDS),
    )
    high = protocol.adjudicate_s1(
        **kwargs,
        senior_rows=_runs((10000.0,) * 8, S1_VALIDATION_SEEDS),
    )
    assert low.status == high.status == "GO_V8L"
    assert low.senior_pooled_m != high.senior_pooled_m

    with pytest.raises(ValueError, match="mechanism.*Full"):
        protocol.adjudicate_s1(
            **{**kwargs, "s1_full_rows": _runs((97.0,) * 8, S1_VALIDATION_SEEDS)},
            senior_rows=_runs((100.0,) * 8, S1_VALIDATION_SEEDS),
        )


def test_s0v_and_s1_stage_results_bind_terminal_status_to_typed_evidence(
    tmp_path,
):
    base = _stage_result(tmp_path)
    s0_pass = _verdict(seeds=S0_VALIDATION_SEEDS)
    accepted_s0 = dataclasses.replace(
        base,
        stage="S0-V",
        status="PASS",
        verdicts=(("frozen_wrapper", s0_pass),),
        selection={"reason": "frozen wrapper passed without recalibration"},
        calibration=None,
        evidence={"frozen_wrapper_unchanged": True},
    )
    assert accepted_s0.status == "PASS"
    with pytest.raises(ValueError, match="S0-V"):
        dataclasses.replace(
            accepted_s0,
            status="PASS",
            verdicts=(("frozen_wrapper", _failed_verdict(seeds=S0_VALIDATION_SEEDS)),),
        )

    mechanism = _s1_mechanism(seeds=S1_VALIDATION_SEEDS)
    decision = protocol.adjudicate_s1(
        mechanism=mechanism,
        s1_full_rows=_runs((98.0,) * 8, S1_VALIDATION_SEEDS),
        s0_wrapper_rows=_runs((99.0,) * 8, S1_VALIDATION_SEEDS),
        senior_rows=_runs((200.0,) * 8, S1_VALIDATION_SEEDS),
        implementation_failures=_s1_implementation_failures(),
        seed_role="s1_validation",
    )
    accepted_s1 = dataclasses.replace(
        base,
        stage="S1-V",
        status="GO_V8L",
        verdicts=(("mechanism", mechanism),),
        selection={"reason": "all gates and incremental thresholds passed"},
        calibration=None,
        evidence={"adjudication_complete": True},
        s1_adjudication=decision,
    )
    assert accepted_s1.status == "GO_V8L"
    with pytest.raises(ValueError, match="S1-V"):
        dataclasses.replace(accepted_s1, status="S1_VALID")


def test_s1_training_stage_requires_exact_ep400_audit(tmp_path):
    base = _stage_result(tmp_path)
    audit = protocol.S1TrainingAudit(
        status="PASS",
        subtype=None,
        completed_batches=25,
        completed_episodes=400,
        policy_generation=25,
        first_traffic_seed=2643,
        last_traffic_seed=3042,
        workers=16,
        candidate_kind="candidate",
        candidate_completed_episodes=400,
        recovery_kind="recovery",
        implementation_failures=tuple(
            (name, 0) for name in protocol.S1_IMPLEMENTATION_CHECKS
        ),
    )
    accepted = dataclasses.replace(
        base,
        stage="S1-T",
        status="PASS",
        verdicts=(),
        selection={"reason": "exact ep400 candidate completed"},
        calibration=None,
        evidence={"training_audit_complete": True},
        s1_training_audit=audit,
    )
    assert accepted.status == "PASS"
    with pytest.raises(ValueError):
        dataclasses.replace(
            accepted,
            s1_training_audit=dataclasses.replace(
                audit, candidate_completed_episodes=200
            ),
        )


@pytest.mark.parametrize("status", ("INVALID", "HARD_EXTERNAL_BLOCKER"))
def test_nonterminal_fail_closed_statuses_require_auditable_reason_and_evidence(
    status, tmp_path
):
    result = _stage_result(tmp_path)
    with pytest.raises(ValueError):
        dataclasses.replace(
            result,
            stage="S0-V",
            status=status,
            verdicts=(),
            selection={},
            calibration=None,
            evidence={},
        )
    audited = dataclasses.replace(
        result,
        stage="S0-V",
        status=status,
        verdicts=(),
        selection={"reason": "checkpoint unavailable"},
        calibration=None,
        evidence={"error_type": "missing_checkpoint"},
    )
    assert audited.status == status


@pytest.mark.parametrize("forged_label", ("+0.025", "0.0250", "2.5e-2"))
def test_s0d_stage_requires_canonical_lambda_labels_and_grid(forged_label, tmp_path):
    result = _stage_result(tmp_path)
    verdicts = list(result.verdicts)
    verdicts[0] = (forged_label, verdicts[0][1])
    with pytest.raises(ValueError):
        dataclasses.replace(result, verdicts=verdicts)

    forged_grid = (False, *LAMBDA_GRID[1:])
    with pytest.raises((TypeError, ValueError)):
        dataclasses.replace(
            result,
            config={"quantile_method": "linear", "lambda_grid": forged_grid},
        )


def test_s0w_stage_serializes_bound_calibration_without_json_key_coercion(tmp_path):
    calibration = calibrate_wrapper(
        _probe_rows(
            (0.1, 0.2, 0.5, 1.0, 1.5, 2.5, 4.0, 8.0),
            (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0),
        ),
        0.20,
    )
    result = StageResult(
        stage="S0-W",
        status="PASS",
        verdicts=(("Narrow", _verdict()),),
        selection={
            "lambda_star": 0.20,
            "band": "Narrow",
            "reason": "first passing band",
        },
        calibration=calibration,
        manifest={"commands": [["python", "evaluate.py"]]},
        config={"quantile_method": "linear", "lambda_grid": list(LAMBDA_GRID)},
        input_artifacts=_input_artifacts(tmp_path / "s0w-inputs"),
        evidence={"band_prefix_complete": True},
    )
    stage_dir = tmp_path / "S0-W"
    write_stage_artifacts(stage_dir, result)
    report = json.loads((stage_dir / "report.json").read_text())
    assert report["calibration"]["probe_hashes"] == [
        {"seed": seed, "sha256": digest}
        for seed, digest in calibration.probe_hashes
    ]
    assert {band["c_star"] for band in report["calibration"]["bands"]} == {
        calibration.c_star
    }


def test_s0w_stage_requires_canonical_band_order_and_lambda_grid(tmp_path):
    calibration = calibrate_wrapper(
        _probe_rows(
            (0.1, 0.2, 0.5, 1.0, 1.5, 2.5, 4.0, 8.0),
            (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0),
        ),
        0.20,
    )
    result = StageResult(
        stage="S0-W",
        status="PASS",
        verdicts=(("Narrow", _failed_verdict()), ("Medium", _verdict())),
        selection={
            "lambda_star": 0.20,
            "band": "Medium",
            "reason": "first passing band",
        },
        calibration=calibration,
        manifest={"commands": [["python", "evaluate.py"]]},
        config={"quantile_method": "linear", "lambda_grid": list(LAMBDA_GRID)},
        input_artifacts=_input_artifacts(tmp_path / "s0w-order-inputs"),
        evidence={"band_prefix_complete": True},
    )
    with pytest.raises(ValueError):
        dataclasses.replace(result, verdicts=tuple(reversed(result.verdicts)))
    with pytest.raises(ValueError):
        dataclasses.replace(
            result,
            config={
                "quantile_method": "linear",
                "lambda_grid": (False, *LAMBDA_GRID[1:]),
            },
        )


@pytest.mark.parametrize("operation", ("hash", "existing_stage_artifact"))
def test_protocol_rejects_fifo_without_blocking(operation, tmp_path):
    fifo_path = tmp_path / "input.fifo"
    os.mkfifo(fifo_path)
    stage_dir = tmp_path / "stage"
    if operation == "existing_stage_artifact":
        stage_dir.mkdir()
        fifo_path = stage_dir / "report.json"
        os.mkfifo(fifo_path)
    script = """
import sys
from pathlib import Path
from algorithms.evaluation.vrc_v8_protocol import hash_file, write_stage_artifacts

try:
    if sys.argv[1] == "hash":
        hash_file(Path(sys.argv[2]))
    else:
        from algorithms.evaluation.test_vrc_v8_protocol import _stage_result
        write_stage_artifacts(Path(sys.argv[3]), _stage_result(Path(sys.argv[4])))
except ValueError:
    raise SystemExit(0)
raise SystemExit(1)
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            operation,
            str(fifo_path),
            str(stage_dir),
            str(tmp_path / "fifo-stage-inputs"),
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=2.0,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_stage_writer_fails_before_emission_on_input_hash_or_existing_mismatch(tmp_path):
    result = _stage_result(tmp_path)
    Path(result.input_artifacts[0].path).write_bytes(b"tampered")
    bad_dir = tmp_path / "bad"
    with pytest.raises(ValueError):
        write_stage_artifacts(bad_dir, result)
    assert not bad_dir.exists() or not tuple(bad_dir.iterdir())

    result = _stage_result(tmp_path / "fresh")
    stage_dir = tmp_path / "existing"
    write_stage_artifacts(stage_dir, result)
    (stage_dir / "report.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError):
        write_stage_artifacts(stage_dir, result)

    sealed_stage_dir = tmp_path / "synthetic_77501_stage"
    with pytest.raises(ValueError):
        write_stage_artifacts(sealed_stage_dir, _stage_result(tmp_path / "sealed-inputs"))
