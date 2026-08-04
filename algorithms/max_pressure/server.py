"""
Max Pressure 算法服务 —— HTTP/JSON 接口，实现
docs/algorithm_interface.md 中定义的三端点契约（协议 v2.0）。

启动：
    uvicorn max_pressure.server:app --host 0.0.0.0 --port 8001
"""

from __future__ import annotations

import logging
import sys
import time
import traceback
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .controller import MaxPressureController

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("max_pressure.server")

# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Max Pressure 信号控制",
    version="2.0.0",
)

# 每个 episode 一个控制器 + 指标采集器
_controller: Optional[MaxPressureController] = None
_current_episode_id: Optional[str] = None

# 指标采集器（延迟导入，避免 evaluation 依赖主逻辑）
_collector: Any = None
_last_result: Any = None


def _get_collector():
    global _collector
    if _collector is None:
        from evaluation.collector import HttpMetricsCollector
        _collector = HttpMetricsCollector(algorithm="MaxPressure")
    return _collector


# ======================================================================
# 中间件：全局异常日志
# ======================================================================


@app.middleware("http")
async def _log_errors(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception:
        logger.exception("未处理的异常 %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": "internal", "detail": traceback.format_exc()},
        )


# ======================================================================
# 端点
# ======================================================================


@app.post("/initialize")
async def initialize(body: Dict[str, Any]) -> Dict[str, Any]:
    global _controller, _current_episode_id, _last_result

    episode_id = body.get("episode_id", "unknown")
    n_intersections = len(body.get("intersections", {}))
    logger.info(
        "POST /initialize  episode=%s  路口数=%d  时段=%s  种子=%s",
        episode_id,
        n_intersections,
        body.get("period", ""),
        body.get("seed", ""),
    )

    try:
        _controller = MaxPressureController(body)
        _current_episode_id = episode_id
        _get_collector().on_initialize(body)
        _last_result = None
    except Exception as exc:
        logger.exception("MaxPressureController 初始化失败")
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "protocol_version": "2.0",
        "episode_id": episode_id,
        "ready": True,
    }


@app.post("/step")
async def step(body: Dict[str, Any]) -> Dict[str, Any]:
    global _controller

    if _controller is None:
        raise HTTPException(status_code=400, detail="未初始化 —— 请先调用 /initialize")

    step_id = body.get("step_id")
    if step_id is None:
        raise HTTPException(status_code=400, detail="缺少 step_id")

    episode_id = body.get("episode_id", "")
    if episode_id != _current_episode_id:
        raise HTTPException(
            status_code=409,
            detail=f"episode 不匹配: 期望 {_current_episode_id}，收到 {episode_id}",
        )

    try:
        t0 = time.perf_counter()
        actions = _controller.compute_actions(body)
        _get_collector().record_latency((time.perf_counter() - t0) * 1000)
        _get_collector().on_step(body)
    except Exception as exc:
        logger.exception("compute_actions 在 step_id=%s 失败", step_id)
        raise HTTPException(status_code=500, detail=str(exc))

    # v2.0 格式：actions.signals.{iid}.target_phase
    signal_actions = {
        iid: {"target_phase": pid}
        for iid, pid in actions.items()
        if pid is not None
    }
    return {
        "protocol_version": "2.0",
        "episode_id": _current_episode_id,
        "step_id": step_id,
        "actions": {"signals": signal_actions, "vehicles": {}},
    }


@app.post("/finish")
async def finish(body: Dict[str, Any]) -> Dict[str, Any]:
    global _controller, _current_episode_id, _last_result

    reason = body.get("reason", "unknown")
    sim_time = body.get("simulation_time", 0.0)
    arrived = body.get("arrived_vehicles", 0)
    departed = body.get("departed_vehicles", 0)

    logger.info(
        "POST /finish  episode=%s  原因=%s  仿真时间=%.1fs  "
        "到达=%d/%d  完成率=%.1f%%",
        _current_episode_id,
        reason,
        sim_time,
        arrived,
        departed,
        (arrived / departed * 100) if departed > 0 else 0.0,
    )

    # 结算指标
    _get_collector().on_finish(body)
    _last_result = _get_collector().result()

    logger.info(
        "指标: 行程=%.1fs 等待=%.1fs 排队=%.2f 吞吐=%.1f/h 延迟=%.3fms 油耗=%.2f",
        _last_result.avg_travel_time_s,
        _last_result.avg_waiting_time_s,
        _last_result.avg_queue_length_veh,
        _last_result.throughput_veh_per_h,
        _last_result.avg_decision_latency_ms,
        _last_result.fuel_intensity_L_per_100km,
    )

    # 释放 episode 状态
    _controller = None
    _current_episode_id = None

    return {"ok": True}


# ======================================================================
# 评估指标 & 健康检查
# ======================================================================


@app.get("/stats")
async def stats() -> Dict[str, Any]:
    """获取最近一次 episode 的 6 大指标。"""
    if _last_result is None:
        return {"error": "还没有完成过仿真"}
    return _last_result.to_dict()


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "algorithm": "max_pressure",
        "initialized": _controller is not None,
        "episode_id": _current_episode_id,
    }
