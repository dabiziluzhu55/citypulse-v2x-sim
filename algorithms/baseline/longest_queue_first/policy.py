"""Example policy using only the public CityPulse Python interface."""

from __future__ import annotations

from typing import Dict

from simulation.sumo.policy import SimulationMetadata, SimulationObservation


class LongestQueuePolicy:
    """Select the official phase whose configured approaches have most stopped cars.

    Current SUMO lanes are not dedicated by turn, so straight and left phases on the
    same axis can tie. Ties rotate after the current phase to keep every movement live.
    """

    def __init__(self) -> None:
        self._metadata: SimulationMetadata | None = None

    def reset(self, metadata: SimulationMetadata) -> None:
        self._metadata = metadata

    def act(self, observation: SimulationObservation) -> Dict[str, int]:
        if self._metadata is None:
            raise RuntimeError("Policy.reset() must be called before act().")
        result = {}
        for intersection_id, current in observation.intersections.items():
            metadata = self._metadata.intersections[intersection_id]
            approach_queues = {
                approach: sum(lane.halting_count for lane in lanes)
                for approach, lanes in current.approaches.items()
            }
            scores = {}
            for phase, (_, approaches) in metadata.phase_movements.items():
                scores[phase] = sum(approach_queues[name] for name in approaches)
            best_score = max(scores.values())
            tied = {phase for phase, score in scores.items() if score == best_score}
            current_index = metadata.phase_order.index(current.current_phase)
            rotated = (
                metadata.phase_order[current_index + 1 :]
                + metadata.phase_order[: current_index + 1]
            )
            result[intersection_id] = next(phase for phase in rotated if phase in tied)
        return result

    def close(self) -> None:
        self._metadata = None

