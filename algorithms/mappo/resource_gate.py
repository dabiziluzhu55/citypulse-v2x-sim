"""Parallel-training resource gate (spec §5.2).

All reads are read-only; no system state is modified. The smoke-derived
fields (ram_peak_gb / gpu_peak_mib / sumo_ok / pending_dropped /
isolation_proven) are filled by the caller via dataclasses.replace after
collecting the live snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ResourceSnapshot:
    load5: float
    cpu_idle: float
    iowait: float
    ram_free_gb: float
    ram_peak_gb: float
    gpu_free_mib: tuple[float, ...]
    gpu_peak_mib: tuple[float, ...]
    sumo_ok: bool
    pending_dropped: int
    isolation_proven: bool


def evaluate_resource_gate(snap: ResourceSnapshot) -> tuple[bool, list[str]]:
    """Return (pass, reasons). All conditions must hold for parallel arms."""

    reasons: list[str] = []
    if snap.load5 > 14.0:
        reasons.append(f"load5 {snap.load5:.1f} > 14")
    if snap.cpu_idle < 0.35:
        reasons.append(f"cpu idle {snap.cpu_idle:.2%} < 35%")
    if snap.iowait >= 0.05:
        reasons.append(f"iowait {snap.iowait:.2%} >= 5%")
    if snap.ram_free_gb < 1.25 * snap.ram_peak_gb:
        reasons.append("RAM free < 1.25x peak")
    for idx, (free, peak) in enumerate(
        zip(snap.gpu_free_mib, snap.gpu_peak_mib, strict=False)
    ):
        if free < 1.25 * peak:
            reasons.append(
                f"GPU {idx} free {free:.0f}MiB < 1.25x peak {peak:.0f}MiB"
            )
    if not snap.sumo_ok:
        reasons.append("SUMO smoke had timeout/restart")
    if snap.pending_dropped != 0:
        reasons.append(f"pending_dropped={snap.pending_dropped}")
    if not snap.isolation_proven:
        reasons.append(
            "no cgroup/SLURM exclusive isolation (taskset alone insufficient)"
        )
    return (len(reasons) == 0), reasons


def collect_resource_snapshot() -> ResourceSnapshot:
    """Read /proc/loadavg, /proc/stat, free, and nvidia-smi (all read-only)."""

    import shutil
    import subprocess
    import time

    _load1, load5, _load15, *_ = (
        float(value)
        for value in Path("/proc/loadavg").read_text(encoding="utf-8").split()[:3]
    )

    def _cpu_times() -> dict[str, float]:
        text = Path("/proc/stat").read_text(encoding="utf-8")
        line = next(
            line for line in text.splitlines() if line.startswith("cpu ")
        )
        parts = line.split()
        keys = [
            "user",
            "nice",
            "system",
            "idle",
            "iowait",
            "irq",
            "softirq",
            "steal",
        ]
        return {
            key: float(parts[index + 1])
            for index, key in enumerate(keys)
            if index + 1 < len(parts)
        }

    t0 = _cpu_times()
    time.sleep(1.0)
    t1 = _cpu_times()
    total = sum(t1.values()) - sum(t0.values())
    cpu_idle = (t1["idle"] - t0["idle"]) / total if total > 0 else 0.0
    iowait = (t1["iowait"] - t0["iowait"]) / total if total > 0 else 0.0

    meminfo = Path("/proc/meminfo").read_text(encoding="utf-8")
    mem_available_kb = next(
        int(line.split()[1])
        for line in meminfo.splitlines()
        if line.startswith("MemAvailable:")
    )
    ram_free_gb = mem_available_kb / (1024**2)  # MemAvailable in kB -> GiB

    gpu_free_mib: tuple[float, ...] = ()
    if shutil.which("nvidia-smi") is not None:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            gpu_free_mib = tuple(
                float(value) for value in result.stdout.split()
            )

    return ResourceSnapshot(
        load5=load5,
        cpu_idle=cpu_idle,
        iowait=iowait,
        ram_free_gb=ram_free_gb,
        ram_peak_gb=0.0,
        gpu_free_mib=gpu_free_mib,
        gpu_peak_mib=(0.0,) * len(gpu_free_mib),
        sumo_ok=True,
        pending_dropped=0,
        isolation_proven=False,
    )
