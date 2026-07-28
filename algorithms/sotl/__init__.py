<<<<<<< HEAD
"""SOTL 自适应信号控制 —— HTTP 算法服务。"""
=======
"""SOTL 自适应信号控制 —— Protocol 2.0 local transport。"""

from __future__ import annotations

from algorithms.sotl.controller import SOTLController

_controller = None

def initialize(payload: dict) -> dict:
    global _controller
    _controller = SOTLController(payload)
    return {
        "protocol_version": "2.0",
        "episode_id": payload["episode_id"],
        "ready": True,
    }

def step(payload: dict) -> dict:
    actions_map = _controller.compute_actions(payload)
    signals = {}
    for iid, phase in actions_map.items():
        if phase is not None:
            signals[iid] = {"target_phase": phase}
    return {
        "protocol_version": "2.0",
        "episode_id": payload["episode_id"],
        "step_id": payload["step_id"],
        "actions": {"signals": signals, "vehicles": {}},
    }

def finish(payload: dict) -> None:
    global _controller
    _controller = None
>>>>>>> origin/feature/rl
