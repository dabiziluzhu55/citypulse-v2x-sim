"""兼容层：实现见 traffic_eval.session_hub"""

from traffic_eval.session_hub import (  # noqa: F401
    MAX_COMPLETED_RESULTS,
    TERMINAL_STATES,
    SessionMetricsHub,
)

__all__ = [
    "MAX_COMPLETED_RESULTS",
    "TERMINAL_STATES",
    "SessionMetricsHub",
]
