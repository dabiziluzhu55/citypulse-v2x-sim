"""SUMO-independent safety state machine for algorithm-controlled phases."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence, Tuple


_EPSILON = 1e-9


class SignalStage(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    CLEARANCE = "CLEARANCE"


class InvalidPhaseAction(ValueError):
    """Raised before SUMO is mutated when an algorithm returns an invalid phase."""


@dataclass(frozen=True)
class TransitionTiming:
    yellow: float
    clearance: float


@dataclass(frozen=True)
class ControllerSnapshot:
    current_phase: int
    stage: SignalStage
    stage_started_at: float
    pending_phase: int | None


class SafePhaseController:
    """Enforces minimum green, yellow and clearance around algorithm actions."""

    def __init__(
        self,
        phase_order: Sequence[int],
        timings: Mapping[int, TransitionTiming | Tuple[float, float]],
        *,
        minimum_green: float = 5.0,
        initial_phase: int | None = None,
        start_time: float = 0.0,
    ) -> None:
        order = tuple(int(value) for value in phase_order)
        if not order or len(order) != len(set(order)):
            raise ValueError("phase_order must be non-empty and unique.")
        if minimum_green < 0:
            raise ValueError("minimum_green cannot be negative.")
        parsed_timings = {}
        for phase in order:
            value = timings[phase]
            timing = value if isinstance(value, TransitionTiming) else TransitionTiming(*value)
            if timing.yellow < 0 or timing.clearance < 0:
                raise ValueError(f"Phase {phase} has a negative transition duration.")
            parsed_timings[phase] = timing
        initial = order[0] if initial_phase is None else int(initial_phase)
        if initial not in order:
            raise ValueError(f"Initial phase {initial} is not in phase_order.")
        self.phase_order = order
        self.timings = parsed_timings
        self.minimum_green = float(minimum_green)
        self.current_phase = initial
        self.stage = SignalStage.GREEN
        self.stage_started_at = float(start_time)
        self.pending_phase: int | None = None
        self._pending_since: float | None = None
        self._last_time = float(start_time)

    def snapshot(self) -> ControllerSnapshot:
        return ControllerSnapshot(
            current_phase=self.current_phase,
            stage=self.stage,
            stage_started_at=self.stage_started_at,
            pending_phase=self.pending_phase,
        )

    def stage_elapsed(self, current_time: float) -> float:
        self._check_time(current_time)
        return max(0.0, float(current_time) - self.stage_started_at)

    def request_phase(self, target_phase: int, current_time: float) -> bool:
        target = int(target_phase)
        if target not in self.phase_order:
            raise InvalidPhaseAction(
                f"Official phase {target} is not one of {self.phase_order}."
            )
        changed = self.advance(current_time)
        if self.stage == SignalStage.GREEN and target == self.current_phase:
            self.pending_phase = None
            self._pending_since = None
            return changed
        self.pending_phase = target
        self._pending_since = float(current_time)
        return self.advance(current_time) or changed

    def advance(self, current_time: float) -> bool:
        now = float(current_time)
        self._check_time(now)
        changed = False
        while True:
            if self.stage == SignalStage.GREEN:
                if self.pending_phase is None or self.pending_phase == self.current_phase:
                    break
                requested_at = self._pending_since if self._pending_since is not None else now
                transition_at = max(
                    self.stage_started_at + self.minimum_green,
                    requested_at,
                )
                if now + _EPSILON < transition_at:
                    break
                self.stage = SignalStage.YELLOW
                self.stage_started_at = transition_at
                changed = True
                continue
            if self.stage == SignalStage.YELLOW:
                transition_at = (
                    self.stage_started_at + self.timings[self.current_phase].yellow
                )
                if now + _EPSILON < transition_at:
                    break
                self.stage = SignalStage.CLEARANCE
                self.stage_started_at = transition_at
                changed = True
                continue
            transition_at = (
                self.stage_started_at + self.timings[self.current_phase].clearance
            )
            if now + _EPSILON < transition_at:
                break
            target = self.pending_phase
            if target is None:
                target = self.current_phase
            self.current_phase = target
            self.pending_phase = None
            self._pending_since = None
            self.stage = SignalStage.GREEN
            self.stage_started_at = transition_at
            changed = True
        self._last_time = now
        return changed

    def _check_time(self, current_time: float) -> None:
        if float(current_time) + _EPSILON < self._last_time:
            raise ValueError(
                f"Simulation time moved backwards: {current_time} < {self._last_time}."
            )
