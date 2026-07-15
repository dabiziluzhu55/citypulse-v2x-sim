"""CityPulse V2X mock backend for frontend development."""

from __future__ import annotations

import asyncio
import contextlib
import mimetypes
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from data import ALGORITHMS, SCENARIO_TEMPLATES, TEMPLATE_MAP_META
from store import store

PUSH_INTERVAL_SECONDS = 5.0
DEFAULT_XIONGAN_3DTILES_DIR = Path(r"E:\city\3dtiles\雄安新区建筑_彩色_3dtiles")
XIONGAN_3DTILES_DIR = Path(
    os.getenv("XIONGAN_3DTILES_DIR", str(DEFAULT_XIONGAN_3DTILES_DIR))
).expanduser()

mimetypes.add_type("model/gltf-binary", ".glb")


class StartRunRequest(BaseModel):
    scenario_id: str
    algorithm: str = "fixed_time"
    cloud_edge_enabled: bool = True
    realtime: bool = True
    step_length: float = 1.0


class ControlRequest(BaseModel):
    command: str


class AlgorithmRequest(BaseModel):
    algorithm_id: str
    parameters: dict[str, Any] = Field(default_factory=dict)


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


app = FastAPI(title="CityPulse V2X Mock API", version="0.1.0", lifespan=lifespan)

if XIONGAN_3DTILES_DIR.is_dir():
    app.mount(
        "/3dtiles/xiongan",
        StaticFiles(directory=XIONGAN_3DTILES_DIR),
        name="xiongan-3dtiles",
    )
else:
    print(
        f"[backend] 3D Tiles directory not found: {XIONGAN_3DTILES_DIR}. "
        "Set XIONGAN_3DTILES_DIR to enable the local building layer."
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _run_or_404(run_id: str):
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    return run


@app.get("/api/v1/scenario-templates")
def list_scenario_templates() -> dict:
    return SCENARIO_TEMPLATES


@app.get("/api/v1/scenario-templates/{template_id}/map-meta")
def get_template_map_meta(template_id: str) -> dict:
    meta = TEMPLATE_MAP_META.get(template_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Template not found: {template_id}")
    return meta


@app.post("/api/v1/scenarios", status_code=201)
def create_scenario(payload: dict[str, Any]) -> dict:
    return store.create_scenario(payload)


@app.post("/api/v1/runs", status_code=201)
def start_run(payload: StartRunRequest) -> dict:
    run = store.create_run(payload.model_dump())
    return {
        "run_id": run.run_id,
        "status": run.status,
        "message": run.message,
    }


@app.post("/api/v1/runs/{run_id}/control")
def control_run(run_id: str, payload: ControlRequest) -> dict:
    try:
        run = store.control_run(run_id, payload.command)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}") from None
    return {"run_id": run.run_id, "status": run.status}


@app.get("/api/v1/runs/{run_id}/status")
def get_run_status(run_id: str) -> dict:
    return _run_or_404(run_id).status_payload()


@app.get("/api/v1/runs/{run_id}/overview")
def get_run_overview(run_id: str) -> dict:
    return _run_or_404(run_id).overview()


@app.get("/api/v1/runs/{run_id}/traffic-state")
def get_traffic_state(run_id: str) -> dict:
    return _run_or_404(run_id).traffic_state()


@app.get("/api/v1/runs/{run_id}/collaboration-state")
def get_collaboration_state(run_id: str) -> dict:
    return _run_or_404(run_id).collaboration_state()


@app.get("/api/v1/algorithms")
def list_algorithms() -> dict:
    return ALGORITHMS


@app.post("/api/v1/runs/{run_id}/algorithm")
def switch_algorithm(run_id: str, payload: AlgorithmRequest) -> dict:
    try:
        run = store.apply_algorithm(run_id, payload.algorithm_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}") from None
    return {"run_id": run.run_id, "algorithm": run.algorithm, "status": "applied"}


@app.get("/api/v1/runs/{run_id}/events")
def get_events(run_id: str) -> dict:
    return _run_or_404(run_id).events()


@app.get("/api/v1/runs/{run_id}/prediction")
def get_prediction(
    run_id: str,
    target: str = Query(default="J12"),
    horizon: int = Query(default=300),
) -> dict:
    return _run_or_404(run_id).prediction(target, horizon)


@app.get("/api/v1/runs/{run_id}/metrics/realtime")
def get_realtime_metrics(run_id: str) -> dict:
    return _run_or_404(run_id).metrics_realtime()


@app.get("/api/v1/runs/{run_id}/metrics/timeseries")
def get_metrics_timeseries(run_id: str) -> dict:
    return _run_or_404(run_id).metrics_timeseries()


@app.get("/api/v1/experiments/{experiment_id}/comparison")
def get_experiment_comparison(experiment_id: str) -> dict:
    run = next(iter(store.runs.values()), None)
    if run is None:
        raise HTTPException(status_code=404, detail="No active runs")
    return run.experiment_comparison(experiment_id)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.websocket("/api/v1/ws")
async def overview_websocket(websocket: WebSocket, run_id: str = Query(default="")):
    await websocket.accept()
    if not run_id or store.get_run(run_id) is None:
        await websocket.close(code=1008)
        return

    push_task = asyncio.create_task(
        _ws_push_loop(websocket, run_id, include_overview=True),
    )
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
