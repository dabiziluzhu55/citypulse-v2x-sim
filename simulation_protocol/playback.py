"""Playback speed constants shared across API and worker."""

from __future__ import annotations

PLAYBACK_SPEEDS = (1.0, 1.25, 1.5, 2.0, 3.0, 5.0)


def normalize_playback_speed(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError("playback speed must be a number, not a boolean.")
    try:
        speed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid playback speed: {value!r}.") from exc
    if speed not in PLAYBACK_SPEEDS:
        raise ValueError(
            f"playback speed must be one of {PLAYBACK_SPEEDS}, got {value!r}."
        )
    return speed
