"""仿真等效城市交通运行指数（TPI）

区间映射来源于GB/T 33171—2016给出的DTP→TPI转换区间
"""

from __future__ import annotations

import math
from typing import Optional

TPI_METHOD = "GB/T33171-2016 Annex C / DTP / piecewise-linear"
TPI_SOURCE = "GB/T33171-2016_Annex_C_DTP_piecewise_linear"

# (dtp_lo, dtp_hi, tpi_lo, tpi_hi, right_closed)
_DTP_BINS: tuple[tuple[float, float, float, float, bool], ...] = (
    (0.0, 0.3, 0.0, 2.0, False),
    (0.3, 0.5, 2.0, 4.0, False),
    (0.5, 0.6, 4.0, 6.0, False),
    (0.6, 0.7, 6.0, 8.0, False),
    (0.7, 1.0, 8.0, 10.0, True),
)

# (tpi_lo, tpi_hi, label, right_closed)
_STATE_BINS: tuple[tuple[float, float, str, bool], ...] = (
    (0.0, 2.0, "畅通", False),
    (2.0, 4.0, "基本畅通", False),
    (4.0, 6.0, "轻度拥堵", False),
    (6.0, 8.0, "中度拥堵", False),
    (8.0, 10.0, "严重拥堵", True),
)


def _in_bin(value: float, lo: float, hi: float, *, right_closed: bool) -> bool:
    if value < lo:
        return False
    if right_closed:
        return value <= hi
    return value < hi


def traffic_state_from_tpi(tpi: float) -> str:
    clamped = min(10.0, max(0.0, float(tpi)))
    for lo, hi, label, right_closed in _STATE_BINS:
        if _in_bin(clamped, lo, hi, right_closed=right_closed):
            return label
    return "严重拥堵"


def tpi_from_dtp(dtp: float) -> tuple[float, str]:
    """由延误时间比（0~1）得到连续 TPI（0~10）及运行状态。"""

    if not math.isfinite(dtp):
        raise ValueError("DTP is not finite")
    if dtp <= 0.0:
        return 0.0, traffic_state_from_tpi(0.0)
    if dtp >= 1.0:
        return 10.0, traffic_state_from_tpi(10.0)

    tpi = 10.0
    for dtp_lo, dtp_hi, tpi_lo, tpi_hi, right_closed in _DTP_BINS:
        if not _in_bin(dtp, dtp_lo, dtp_hi, right_closed=right_closed):
            continue
        span = dtp_hi - dtp_lo
        if span <= 0.0:
            tpi = tpi_lo
        else:
            ratio = (dtp - dtp_lo) / span
            tpi = tpi_lo + ratio * (tpi_hi - tpi_lo)
        break
    tpi = min(10.0, max(0.0, tpi))
    return tpi, traffic_state_from_tpi(tpi)


def tpi_from_optional_dtp(
    dtp: Optional[float],
) -> tuple[Optional[float], Optional[str], Optional[str]]:
    if dtp is None:
        return None, None, None
    tpi, state = tpi_from_dtp(float(dtp))
    return tpi, state, TPI_METHOD
