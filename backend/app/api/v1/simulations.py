# 仿真生命周期接口与WebSocket实时推送SimulationService

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from queue import Empty

from fastapi import APIRouter, Depends, Query, Response, WebSocket, WebSocketDisconnect, status

from ...schemas.events import EventCreatedResponse, EventRequest
from ...schemas.intelligence import IntelligencePayload
from ...schemas.simulations import (
    MetricsResponse,
    SetPlaybackSpeedRequest,
    SimulationPlaybackResponse,
    SimulationSessionListResponse,
    SimulationStatusResponse,
    StartSimulationRequest,
    StartSimulationResponse,
    StopSimulationResponse,
)
from ...services.simulation_service import TERMINAL_STATES, SimulationService
from ..deps import get_simulation_service

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/simulations", response_model=SimulationSessionListResponse)
def list_simulations(
    state: str | None = Query(default=None, description="按状态筛选，例如 QUEUED/RUNNING"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    service: SimulationService = Depends(get_simulation_service),
) -> SimulationSessionListResponse:
    payload = service.list_sessions(state=state, offset=offset, limit=limit)
    return SimulationSessionListResponse(**payload)


@router.post(
    "/simulations",
    response_model=StartSimulationResponse,
    status_code=status.HTTP_201_CREATED,
)
def start_simulation(
    request_body: StartSimulationRequest,
    service: SimulationService = Depends(get_simulation_service),
) -> StartSimulationResponse:
    session_id, snapshot = service.start(request_body)
    return StartSimulationResponse(
        session_id=session_id,
        state=snapshot.state,
        status_url=f"/api/v1/simulations/{session_id}",
        websocket_url=f"/api/v1/simulations/{session_id}/stream",
        metrics_url=f"/api/v1/simulations/{session_id}/metrics",
        intelligence_url=f"/api/v1/simulations/{session_id}/intelligence",
        scenario_preset_id=request_body.scenario_preset_id,
    )


@router.get("/simulations/{session_id}", response_model=SimulationStatusResponse)
def get_simulation_status(
    session_id: str,
    service: SimulationService = Depends(get_simulation_service),
) -> SimulationStatusResponse:
    return SimulationStatusResponse(**service.snapshot(session_id))


@router.get("/simulations/{session_id}/metrics", response_model=MetricsResponse)
def get_simulation_metrics(
    session_id: str,
    service: SimulationService = Depends(get_simulation_service),
) -> MetricsResponse:
    return MetricsResponse(**service.get_metrics(session_id))


@router.get(
    "/simulations/{session_id}/intelligence",
    response_model=IntelligencePayload,
)
def get_simulation_intelligence(
    session_id: str,
    service: SimulationService = Depends(get_simulation_service),
) -> IntelligencePayload:
    return IntelligencePayload(**service.get_intelligence(session_id))


@router.get("/simulations/{session_id}/event-detection")
def get_simulation_event_detection(
    session_id: str,
    service: SimulationService = Depends(get_simulation_service),
) -> dict:
    return service.get_intelligence(session_id)["event_detection"]


@router.get("/simulations/{session_id}/prediction")
def get_simulation_prediction(
    session_id: str,
    service: SimulationService = Depends(get_simulation_service),
) -> dict:
    return service.get_intelligence(session_id)["prediction"]


@router.post("/simulations/{session_id}/stop", response_model=StopSimulationResponse)
def stop_simulation(
    session_id: str,
    service: SimulationService = Depends(get_simulation_service),
) -> StopSimulationResponse:
    snapshot = service.stop(session_id)
    return StopSimulationResponse(session_id=session_id, state=snapshot.state)


@router.post(
    "/simulations/{session_id}/pause",
    response_model=SimulationPlaybackResponse,
)
def pause_simulation(
    session_id: str,
    service: SimulationService = Depends(get_simulation_service),
) -> SimulationPlaybackResponse:
    snapshot = service.pause(session_id)
    return SimulationPlaybackResponse(
        session_id=session_id,
        state=snapshot.state,
        playback_speed=snapshot.playback_speed,
    )


@router.post(
    "/simulations/{session_id}/resume",
    response_model=SimulationPlaybackResponse,
)
def resume_simulation(
    session_id: str,
    service: SimulationService = Depends(get_simulation_service),
) -> SimulationPlaybackResponse:
    snapshot = service.resume(session_id)
    return SimulationPlaybackResponse(
        session_id=session_id,
        state=snapshot.state,
        playback_speed=snapshot.playback_speed,
    )


@router.post(
    "/simulations/{session_id}/playback-speed",
    response_model=SimulationPlaybackResponse,
)
def set_simulation_playback_speed(
    session_id: str,
    request_body: SetPlaybackSpeedRequest,
    service: SimulationService = Depends(get_simulation_service),
) -> SimulationPlaybackResponse:
    snapshot = service.set_playback_speed(session_id, request_body.playback_speed)
    return SimulationPlaybackResponse(
        session_id=session_id,
        state=snapshot.state,
        playback_speed=snapshot.playback_speed,
    )


@router.post(
    "/simulations/{session_id}/events",
    response_model=EventCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_simulation_event(
    session_id: str,
    request_body: EventRequest,
    service: SimulationService = Depends(get_simulation_service),
) -> EventCreatedResponse:
    event_id = service.add_event(session_id, request_body)
    return EventCreatedResponse(event_id=event_id)


@router.delete("/simulations/{session_id}/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_simulation_event(
    session_id: str,
    event_id: str,
    service: SimulationService = Depends(get_simulation_service),
) -> Response:
    service.cancel_event(session_id, event_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.websocket("/simulations/{session_id}/stream")
async def simulation_stream(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    app = websocket.app
    mode = getattr(app.state, "simulation_manager_mode", "local")
    if not app.state.artifacts_ready:
        await websocket.close(code=1011)
        return
    if mode == "local" and not app.state.sumo_home_configured:
        await websocket.close(code=1011)
        return
    if not getattr(app.state, "simulation_manager_ready", False):
        await websocket.close(code=1011)
        return
    if app.state.simulation_service is None:
        await websocket.close(code=1011)
        return

    service: SimulationService = app.state.simulation_service
    try:
        subscription = service.subscribe(session_id)
    except Exception:
        logger.exception("WebSocket subscribe failed for session %s", session_id)
        await websocket.close(code=1011)
        return

    logger.info("WebSocket connected for session %s", session_id)

    async def _send_snapshot(snapshot) -> bool:
        terminal = snapshot.state in TERMINAL_STATES
        serializer = (
            service.serialize_terminal_snapshot
            if terminal
            else service.serialize_snapshot
        )

        serialize_started = time.perf_counter()
        message = await asyncio.to_thread(
            lambda: json.dumps(
                {
                    "type": "snapshot",
                    "data": serializer(snapshot),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        serialize_duration_ms = (
            time.perf_counter() - serialize_started
        ) * 1000.0
        if serialize_duration_ms >= 200.0:
            logger.warning(
                "Slow WebSocket snapshot serialization: "
                "session=%s sequence=%s vehicles=%s duration_ms=%.1f",
                snapshot.session_id,
                snapshot.sequence,
                len(snapshot.vehicles),
                serialize_duration_ms,
            )

        await websocket.send_text(message)
        return terminal
    try:
        # 支持从QUEUED一直推送到终态
        initial_snapshot = subscription.get(timeout=2.0)
        if await _send_snapshot(initial_snapshot):
            return

        while True:
            try:
                snapshot = await asyncio.to_thread(subscription.get, 2.0)
            except Empty:
                await websocket.send_json(
                    {
                        "type": "heartbeat",
                        "session_id": session_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )
                continue

            if await _send_snapshot(snapshot):
                break
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected for session %s", session_id)
    except Exception:
        logger.exception("WebSocket stream failed for session %s", session_id)
        await websocket.close(code=1011)
    finally:
        subscription.close()
        logger.info("WebSocket closed for session %s", session_id)
