"""High-frequency, non-controlling evaluation observer.

Enable it through ``SimulationConfig.ai_observer_module`` using
``algorithms.evaluation.observer``.  It shares the collector created by a local
IPPO/CoSLight controller and replaces sparse decision-frame traffic samples.
"""

from __future__ import annotations

from typing import Any, Dict

from . import runtime


def initialize(metadata: Dict[str, Any]) -> None:
    runtime.enable_high_frequency_observer(metadata)


def on_frame(frame: Dict[str, Any]) -> None:
    runtime.observe_frame(frame)


def finish(summary: Dict[str, Any]) -> None:
    runtime.finish(summary)
