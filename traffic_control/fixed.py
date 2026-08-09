"""固定配时控制模式
"""

from __future__ import annotations

KERNEL_MODE = "fixed"


def describe() -> dict[str, str]:
    return {
        "control_mode": "fixed",
        "kernel_mode": KERNEL_MODE,
        "note": "Native SUMO fixed timing; no local Protocol 2.0 controller.",
    }
