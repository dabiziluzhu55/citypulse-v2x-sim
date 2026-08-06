from __future__ import annotations

from algorithms.mappo.ep32_gate import evaluate_arm_gate, select_arm


def test_arm_passes_gate() -> None:
    stats = {
        "waiting_delta_vs_m10": -0.4,
        "arrival_delta_vs_m10": 1.0,
        "waiting_vs_vanilla_ucb95": 0.3,
        "target_duplicate_rate": 0.01,
        "win_rate_vs_m10": 6 / 8,
        "nan_inf": 0,
    }
    ok, reasons = evaluate_arm_gate(stats)
    assert ok is True
    assert reasons == []


def test_arm_fails_on_duplicate_rate() -> None:
    stats = {"target_duplicate_rate": 0.5, "waiting_delta_vs_m10": -0.1}
    ok, reasons = evaluate_arm_gate(stats)
    assert ok is False
    assert any("duplicate" in reason for reason in reasons)


def test_arm_fails_on_nonfinite_values() -> None:
    stats = {
        "waiting_delta_vs_m10": -0.4,
        "target_duplicate_rate": 0.01,
        "nan_inf": 3,
    }
    ok, reasons = evaluate_arm_gate(stats)
    assert ok is False
    assert any("non-finite" in reason for reason in reasons)


def test_select_arm_both_pass_prefers_m1_a_unless_b_proves_value() -> None:
    a = {"ok": True, "waiting_mean": 8.0, "arrival_mean": 70.0}
    b = {
        "ok": True,
        "waiting_mean": 7.8,
        "arrival_mean": 69.5,
        "head_to_head_win_rate": 4 / 8,
    }
    assert select_arm(a, b) == "m1_a"
    b2 = {
        "ok": True,
        "waiting_mean": 7.7,
        "arrival_mean": 70.0,
        "head_to_head_win_rate": 6 / 8,
    }
    assert select_arm(a, b2) == "m1_b"


def test_select_arm_prefers_the_only_passing_arm() -> None:
    a = {"ok": False, "waiting_mean": 8.0, "arrival_mean": 70.0}
    b = {
        "ok": True,
        "waiting_mean": 7.5,
        "arrival_mean": 71.0,
        "head_to_head_win_rate": 7 / 8,
    }
    assert select_arm(a, b) == "m1_b"
    assert select_arm(b, a) == "m1_a"
    assert select_arm(a, a) == "none"
