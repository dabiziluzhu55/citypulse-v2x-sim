"""CityPulse V2X mock backend for frontend development."""

from __future__ import annotations

import asyncio
import contextlib
import mimetypes
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from data import ALGORITHMS, SCENARIO_TEMPLATES, TEMPLATE_MAP_META
from models import (
    AlgorithmRequest,
    AlgorithmsResponse,
    AlgorithmSwitchResponse,
    CollaborationStateResponse,
    ControlRequest,
    ControlResponse,
    CreateScenarioRequest,
    CreateScenarioResponse,
    ErrorResponse,
    EventsResponse,
    ExperimentComparisonResponse,
    HealthResponse,
    MapMeta,
    MetricsTimeseriesResponse,
    PredictionResponse,
    RealtimeMetricsResponse,
    RunOverviewResponse,
    RunStatusResponse,
    ScenarioTemplatesResponse,
    StartRunRequest,
    StartRunResponse,
    TrafficStateResponse,
)
from store import store

PUSH_INTERVAL_SECONDS = 5.0
API_BASE_PATH = "/api/v1"
RUN_WEBSOCKET_PATH = f"{API_BASE_PATH}/ws/runs/{{run_id}}"
DEFAULT_XIONGAN_3DTILES_DIR = Path(r"E:\city\3dtiles\雄安新区建筑_彩色_3dtiles")
XIONGAN_3DTILES_DIR = Path(
    os.getenv("XIONGAN_3DTILES_DIR", str(DEFAULT_XIONGAN_3DTILES_DIR))
).expanduser()
XIONGAN_TILESET_FILE = XIONGAN_3DTILES_DIR / "tileset.json"
TILES_AVAILABLE = XIONGAN_TILESET_FILE.is_file()

mimetypes.add_type("model/gltf-binary", ".glb")
mimetypes.add_type("application/octet-stream", ".b3dm")
mimetypes.add_type("application/octet-stream", ".i3dm")
mimetypes.add_type("application/octet-stream", ".pnts")
mimetypes.add_type("application/octet-stream", ".cmpt")

OPENAPI_TAGS = [
    {"name": "系统", "description": "服务健康状态与资源可用性。"},
    {"name": "场景", "description": "场景模板、地图元数据与场景创建。"},
    {"name": "仿真", "description": "仿真启动、控制与运行状态。"},
    {"name": "态势", "description": "运行总览与实时交通态势。"},
    {"name": "协同", "description": "车、路、云协同状态。"},
    {"name": "算法", "description": "交通控制算法列表与运行时切换。"},
    {"name": "事件", "description": "交通事件识别与短时预测。"},
    {"name": "指标", "description": "实时、时序与实验对比指标。"},
]

NOT_FOUND_RESPONSE = {404: {"model": ErrorResponse, "description": "资源不存在"}}


async def _ws_push_loop(
    websocket: WebSocket,
    run_id: str,
    *,
    include_overview: bool = False,
) -> None:
    tick = 0
    try:
        while True:
            run = store.get_run(run_id)
            if run is None:
                await asyncio.sleep(PUSH_INTERVAL_SECONDS)
                continue

            if include_overview:
                await websocket.send_json(run.ws_overview())

            await websocket.send_json(run.ws_traffic_delta())
            await websocket.send_json(run.ws_collaboration_delta())

            tick += 1
            if tick % 6 == 0:
                await websocket.send_json(run.ws_event_detected())

            await asyncio.sleep(PUSH_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception:
        return


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(
    title="CityPulse V2X Mock API",
    summary="CityPulse 前后端联调接口",
    description=(
        "为 CityPulse 前端提供 HTTP API、实时 WebSocket 和雄安 3D Tiles 静态资源。"
        "WebSocket 主入口为 `/api/v1/ws/runs/{run_id}`；Swagger UI 仅直接测试 HTTP 接口。"
    ),
    version="0.2.0",
    lifespan=lifespan,
    openapi_tags=OPENAPI_TAGS,
)

if TILES_AVAILABLE:
    app.mount(
        "/3dtiles/xiongan",
        StaticFiles(directory=XIONGAN_3DTILES_DIR),
        name="xiongan-3dtiles",
    )
else:
    print(
        f"[backend] 3D Tiles entry not found: {XIONGAN_TILESET_FILE}. "
        "Set XIONGAN_3DTILES_DIR to the directory containing tileset.json."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _run_or_404(run_id: str):
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    return run


@app.get(
    "/api/v1/scenario-templates",
    response_model=ScenarioTemplatesResponse,
    tags=["场景"],
    summary="获取场景模板",
    description="返回前端场景构建面板使用的模板和默认地图视野。",
)
def list_scenario_templates() -> dict:
    return SCENARIO_TEMPLATES


@app.get(
    "/api/v1/scenario-templates/{template_id}/map-meta",
    response_model=MapMeta,
    tags=["场景"],
    summary="获取模板地图元数据",
    responses=NOT_FOUND_RESPONSE,
)
def get_template_map_meta(template_id: str) -> dict:
    meta = TEMPLATE_MAP_META.get(template_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Template not found: {template_id}")
    return meta


@app.post(
    "/api/v1/scenarios",
    response_model=CreateScenarioResponse,
    status_code=201,
    tags=["场景"],
    summary="创建仿真场景",
    description="接收前端场景配置并返回生成的场景标识及 SUMO 文件路径。",
)
def create_scenario(payload: CreateScenarioRequest) -> dict:
    return store.create_scenario(payload.model_dump())


@app.post(
    "/api/v1/runs",
    response_model=StartRunResponse,
    status_code=201,
    tags=["仿真"],
    summary="启动仿真运行",
)
def start_run(payload: StartRunRequest) -> dict:
    run = store.create_run(payload.model_dump())
    return {"run_id": run.run_id, "status": run.status, "message": run.message}


@app.post(
    "/api/v1/runs/{run_id}/control",
    response_model=ControlResponse,
    tags=["仿真"],
    summary="控制仿真运行",
    description="支持 pause、resume、stop、reset 和 step。",
    responses=NOT_FOUND_RESPONSE,
)
def control_run(run_id: str, payload: ControlRequest) -> dict:
    try:
        run = store.control_run(run_id, payload.command.value)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}") from None
    return {"run_id": run.run_id, "status": run.status}


@app.get(
    "/api/v1/runs/{run_id}/status",
    response_model=RunStatusResponse,
    tags=["仿真"],
    summary="查询运行状态",
    responses=NOT_FOUND_RESPONSE,
)
def get_run_status(run_id: str) -> dict:
    return _run_or_404(run_id).status_payload()


@app.get(
    "/api/v1/runs/{run_id}/overview",
    response_model=RunOverviewResponse,
    tags=["态势"],
    summary="获取运行总览",
    responses=NOT_FOUND_RESPONSE,
)
def get_run_overview(run_id: str) -> dict:
    return _run_or_404(run_id).overview()


@app.get(
    "/api/v1/runs/{run_id}/traffic-state",
    response_model=TrafficStateResponse,
    tags=["态势"],
    summary="获取实时交通状态",
    responses=NOT_FOUND_RESPONSE,
)
def get_traffic_state(run_id: str) -> dict:
    return _run_or_404(run_id).traffic_state()


@app.get(
    "/api/v1/runs/{run_id}/collaboration-state",
    response_model=CollaborationStateResponse,
    tags=["协同"],
    summary="获取车路云协同状态",
    responses=NOT_FOUND_RESPONSE,
)
def get_collaboration_state(run_id: str) -> dict:
    return _run_or_404(run_id).collaboration_state()


@app.get(
    "/api/v1/algorithms",
    response_model=AlgorithmsResponse,
    tags=["算法"],
    summary="获取控制算法列表",
)
def list_algorithms() -> dict:
    return ALGORITHMS


@app.post(
    "/api/v1/runs/{run_id}/algorithm",
    response_model=AlgorithmSwitchResponse,
    tags=["算法"],
    summary="切换运行算法",
    responses=NOT_FOUND_RESPONSE,
)
def switch_algorithm(run_id: str, payload: AlgorithmRequest) -> dict:
    try:
        run = store.apply_algorithm(run_id, payload.algorithm_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}") from None
    return {"run_id": run.run_id, "algorithm": run.algorithm, "status": "applied"}


@app.get(
    "/api/v1/runs/{run_id}/events",
    response_model=EventsResponse,
    tags=["事件"],
    summary="获取交通事件",
    responses=NOT_FOUND_RESPONSE,
)
def get_events(run_id: str) -> dict:
    return _run_or_404(run_id).events()


@app.get(
    "/api/v1/runs/{run_id}/prediction",
    response_model=PredictionResponse,
    tags=["事件"],
    summary="获取交通流预测",
    responses=NOT_FOUND_RESPONSE,
)
def get_prediction(
    run_id: str,
    target: str = Query(default="J12", min_length=1, description="目标路口 ID"),
    horizon: int = Query(default=300, ge=1, le=3600, description="预测窗口，单位秒"),
) -> dict:
    return _run_or_404(run_id).prediction(target, horizon)


@app.get(
    "/api/v1/runs/{run_id}/metrics/realtime",
    response_model=RealtimeMetricsResponse,
    tags=["指标"],
    summary="获取实时指标",
    responses=NOT_FOUND_RESPONSE,
)
def get_realtime_metrics(run_id: str) -> dict:
    return _run_or_404(run_id).metrics_realtime()


@app.get(
    "/api/v1/runs/{run_id}/metrics/timeseries",
    response_model=MetricsTimeseriesResponse,
    tags=["指标"],
    summary="获取指标时序数据",
    responses=NOT_FOUND_RESPONSE,
)
def get_metrics_timeseries(run_id: str) -> dict:
    return _run_or_404(run_id).metrics_timeseries()


@app.get(
    "/api/v1/experiments/{experiment_id}/comparison",
    response_model=ExperimentComparisonResponse,
    tags=["指标"],
    summary="获取实验算法对比",
    responses={404: {"model": ErrorResponse, "description": "没有可用运行"}},
)
def get_experiment_comparison(experiment_id: str) -> dict:
    run = next(iter(store.runs.values()), None)
    if run is None:
        raise HTTPException(status_code=404, detail="No active runs")
    return run.experiment_comparison(experiment_id)


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["系统"],
    summary="检查后端与 3D Tiles 状态",
)
def health() -> dict:
    return {
        "status": "ok",
        "api_base_path": API_BASE_PATH,
        "websocket_path": RUN_WEBSOCKET_PATH,
        "tiles_available": TILES_AVAILABLE,
        "tiles_url": "/3dtiles/xiongan/tileset.json" if TILES_AVAILABLE else None,
    }


@app.websocket("/api/v1/ws")
async def overview_websocket(websocket: WebSocket, run_id: str = Query(default="")):
    """兼容入口：推送 overview、traffic_state、collaboration_state 和事件。"""
    await websocket.accept()
    if not run_id or store.get_run(run_id) is None:
        await websocket.close(code=1008)
        return

    push_task = asyncio.create_task(_ws_push_loop(websocket, run_id, include_overview=True))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        push_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await push_task


@app.websocket("/api/v1/ws/runs/{run_id}")
async def run_websocket(websocket: WebSocket, run_id: str):
    """前端主连接：推送交通、协同和事件增量。"""
    await websocket.accept()
    if store.get_run(run_id) is None:
        await websocket.close(code=1008)
        return

    push_task = asyncio.create_task(_ws_push_loop(websocket, run_id))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        push_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await push_task
