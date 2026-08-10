"""仿真播放倍速校验：与SUMO SimulationManager允许值保持一致"""

from __future__ import annotations

from simulation.sumo import PLAYBACK_SPEEDS

ALLOWED_PLAYBACK_SPEEDS: tuple[float, ...] = PLAYBACK_SPEEDS


def validate_playback_speed(value: float) -> float:
    if value not in ALLOWED_PLAYBACK_SPEEDS:
        allowed = ", ".join(str(speed) for speed in ALLOWED_PLAYBACK_SPEEDS)
        raise ValueError(
            f"playback_speed must be one of [{allowed}], got {value!r}."
        )
    return value
