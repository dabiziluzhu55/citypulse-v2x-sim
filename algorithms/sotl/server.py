"""
SOTL 自适应信号控制 —— HTTP/JSON 算法服务（协议 v2.0）。

启动：
    uvicorn sotl.server:app --host 0.0.0.0 --port 8002
"""

from __future__ import annotations

import logging
import sys
import time
import traceback
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .controller import SOTLController

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("sotl.server")

app = FastAPI(title="SOTL 信号控制", version="2.0.0")

_controller: Optional[SOTLController] = None
_current_episode_id: Optional[str] = None

_collector: Any = None
_last_result: Any = None


def _get_collector():
    global _collector
    if _collector is None:
        from evaluation.collector import HttpMetricsCollector
        _collector = HttpMetricsCollector(algorithm="SOTL")
    return _collector


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


@app.post("/initialize")
async def initialize(body: Dict[str, Any]) -> Dict[str, Any]:
    global _controller, _current_episode_id, _last_result

    episode_id = body.get("episode_id", "unknown")
    logger.info(
        "POST /initialize  episode=%s  路口数=%d",
        episode_id,
        len(body.get("intersections", {})),
    )

    try:
        _controller = SOTLController(body)
        _current_episode_id = episode_id
        _get_collector().on_initialize(body)
        _last_result = None
    except Exception as exc:
        logger.exception("SOTLController 初始化失败")
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

    logger.info(
        "POST /finish  episode=%s  reason=%s",
        _current_episode_id,
        body.get("reason", "unknown"),
    )

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

    _controller = None
    _current_episode_id = None

    return {"ok": True}


@app.get("/stats")
async def stats() -> Dict[str, Any]:
    if _last_result is None:
        return {"error": "还没有完成过仿真"}
    return _last_result.to_dict()


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "algorithm": "sotl",
        "initialized": _controller is not None,
        "episode_id": _current_episode_id,
    }
