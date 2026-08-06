from __future__ import annotations

from algorithms.mappo.resource_gate import (
    ResourceSnapshot,
    evaluate_resource_gate,
)


def test_gate_passes_when_idle() -> None:
    snap = ResourceSnapshot(
        load5=8.0,
        cpu_idle=0.5,
        iowait=0.01,
        ram_free_gb=64.0,
        ram_peak_gb=8.0,
        gpu_free_mib=(20000.0, 20000.0),
        gpu_peak_mib=(4000.0, 4000.0),
        sumo_ok=True,
        pending_dropped=0,
        isolation_proven=True,
    )
    ok, reasons = evaluate_resource_gate(snap)
    assert ok is True
    assert reasons == []


def test_gate_fails_without_isolation() -> None:
    snap = ResourceSnapshot(
        load5=8.0,
        cpu_idle=0.5,
        iowait=0.01,
        ram_free_gb=64.0,
        ram_peak_gb=8.0,
        gpu_free_mib=(20000.0, 20000.0),
        gpu_peak_mib=(4000.0, 4000.0),
        sumo_ok=True,
        pending_dropped=0,
        isolation_proven=False,
    )
    ok, reasons = evaluate_resource_gate(snap)
    assert ok is False
    assert any("isolation" in reason for reason in reasons)


def test_gate_fails_on_each_violation() -> None:
    snap = ResourceSnapshot(
        load5=16.0,
        cpu_idle=0.2,
        iowait=0.08,
        ram_free_gb=4.0,
        ram_peak_gb=8.0,
        gpu_free_mib=(1000.0, 20000.0),
        gpu_peak_mib=(4000.0, 4000.0),
        sumo_ok=False,
        pending_dropped=2,
        isolation_proven=True,
    )
    ok, reasons = evaluate_resource_gate(snap)
    assert ok is False
    joined = "\n".join(reasons)
    for expected in ("load5", "cpu idle", "iowait", "RAM free", "GPU 0", "SUMO", "pending_dropped"):
        assert expected in joined, expected
