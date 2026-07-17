"""
IPPO 算法服务 —— HTTP/JSON 推理接口（协议 v2.0）。

支持两种模式：
  • random —— 未训练时的随机策略（默认）
  • model  —— 加载训练好的 PPO 模型

启动：
  IPPO_MODE=random uvicorn ippo.server:app --host 0.0.0.0 --port 8003
"""

from __future__ import annotations

import logging
import os
import sys
import time
import traceback
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .controller import IPPOController

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("ippo.server")

_MODE = os.environ.get("IPPO_MODE", "random")
_MODEL_PATH = os.environ.get("IPPO_MODEL_PATH", "")

app = FastAPI(title="IPPO 信号控制", version="2.0.0")

_controller: Optional[IPPOController] = None
_current_episode_id: Optional[str] = None

_collector: Any = None
_last_result: Any = None


def _get_collector():
    global _collector
    if _collector is None:
        from evaluation.collector import HttpMetricsCollector
        _collector = HttpMetricsCollector(algorithm="IPPO")
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
        "POST /initialize  episode=%s  路口数=%d  mode=%s",
        episode_id,
        len(body.get("intersections", {})),
        _MODE,
    )

    try:
        _controller = IPPOController(body, mode=_MODE, model_path=_MODEL_PATH if _MODEL_PATH else None)
        _current_episode_id = episode_id
        _get_collector().on_initialize(body)
        _last_result = None
    except Exception as exc:
        logger.exception("IPPOController 初始化失败")
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
        "algorithm": "ippo",
        "mode": _MODE,
        "initialized": _controller is not None,
        "episode_id": _current_episode_id,
    }
