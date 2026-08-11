"""Action-aware MaxPressure phase regret for reward shaping.

Shared by IPPO and MAPPO controllers.  Must produce bit-identical results
given the same intersection metadata and lane snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class PressureRegretResult:
    """Pressure shaping output for one decision step."""

    regret: float          # r^{MP} in [-1, 0]
    selected_pressure: float
    max_pressure: float
    min_pressure: float
    pressure_range: float  # P_max - P_min
    mp_agreement: int      # 1 if |P(selected) - P_max| <= epsilon, else 0
    all_pressures: dict[int, float]  # phase_id -> pressure


def density_gate(
    incoming_occupancy_pct: float,
    *,
    threshold: float = 40.0,
    alpha_base: float = 0.10,
    density_decay: float = 0.75,
) -> tuple[float, float]:
    """Compute density factor and effective alpha from pre-action occupancy.

    Args:
        incoming_occupancy_pct: average incoming lane occupancy (0-100 scale).
        threshold: occupancy at which density factor reaches 1.0.
        alpha_base: maximum alpha at zero density.
        density_decay: how quickly alpha drops with density.

    Returns:
        (density, alpha) tuple.
    """
    import numpy as np
    d = float(np.clip(incoming_occupancy_pct / threshold, 0.0, 1.0))
    alpha = alpha_base * (1.0 - density_decay * d)
    return d, float(np.clip(alpha, 0.0, alpha_base))


class PressureShaper:
    """Compute per-phase MaxPressure scores from lane halting counts.

    Formula (matches traffic_control/max_pressure.py):
        P(phase) = sum_{c in phase} w_c * (q_up - q_down)

    where q_up = halting_count on the connection's from_lane,
          q_down = halting_count on the connection's to_lane,
          w_c = 1.0 (protected) or 0.5 (permissive).

    No max(0, *) clipping - negative downstream pressure is preserved.
    """

    def __init__(
        self,
        phase_connections: Mapping[int, list[tuple[str, str, float]]],
        *,
        epsilon: float = 1e-6,
    ) -> None:
        self._phase_connections: dict[int, list[tuple[str, str, float]]] = {
            int(k): [(str(fl), str(tl), float(w)) for fl, tl, w in v]
            for k, v in phase_connections.items()
        }
        self._epsilon = float(epsilon)

    def compute_phase_pressures(
        self,
        lanes: Mapping[str, Mapping[str, object]],
        legal_phases: list[int],
    ) -> dict[int, float]:
        """Compute raw pressure for each legal phase.

        Args:
            lanes: lane_id -> {halting_count: float, ...} observation.
            legal_phases: phase indices currently executable.

        Returns:
            {phase_id: pressure} for legal phases only.
        """
        pressures: dict[int, float] = {}
        for phase_id in legal_phases:
            connections = self._phase_connections.get(phase_id, [])
            total = 0.0
            for from_lane, to_lane, weight in connections:
                upstream = float(
                    lanes.get(from_lane, {}).get("halting_count", 0.0)
                    if isinstance(lanes.get(from_lane), Mapping)
                    else 0.0
                )
                downstream = float(
                    lanes.get(to_lane, {}).get("halting_count", 0.0)
                    if isinstance(lanes.get(to_lane), Mapping)
                    else 0.0
                )
                total += weight * (upstream - downstream)
            pressures[phase_id] = total
        return pressures

    def compute_pressure_regret(
        self,
        lanes: Mapping[str, Mapping[str, object]],
        legal_phases: list[int],
        selected_phase: int,
    ) -> PressureRegretResult:
        """Compute normalized pressure regret for the selected action.

        r^{MP} = clip((P(selected) - P_max) / (P_max - P_min + epsilon), -1, 0)

        If P_max - P_min <= epsilon, regret = 0 (all phases effectively equal).

        Args:
            lanes: lane_id -> {halting_count: float, ...} snapshot.
            legal_phases: phase indices currently executable (A^{legal}).
            selected_phase: the phase the agent chose.

        Returns:
            PressureRegretResult with regret in [-1, 0].
        """
        pressures = self.compute_phase_pressures(lanes, legal_phases)
        if not pressures:
            return PressureRegretResult(
                regret=0.0,
                selected_pressure=0.0,
                max_pressure=0.0,
                min_pressure=0.0,
                pressure_range=0.0,
                mp_agreement=1,
                all_pressures={},
            )
        p_selected = pressures.get(selected_phase, 0.0)
        p_max = max(pressures.values())
        p_min = min(pressures.values())
        p_range = p_max - p_min
        if p_range <= self._epsilon:
            return PressureRegretResult(
                regret=0.0,
                selected_pressure=p_selected,
                max_pressure=p_max,
                min_pressure=p_min,
                pressure_range=p_range,
                mp_agreement=1,
                all_pressures=pressures,
            )
        import numpy as np
        regret = float(
            np.clip((p_selected - p_max) / (p_range + self._epsilon), -1.0, 0.0)
        )
        mp_agreement = 1 if abs(p_selected - p_max) <= self._epsilon else 0
        return PressureRegretResult(
            regret=regret,
            selected_pressure=p_selected,
            max_pressure=p_max,
            min_pressure=p_min,
            pressure_range=p_range,
            mp_agreement=mp_agreement,
            all_pressures=pressures,
        )
