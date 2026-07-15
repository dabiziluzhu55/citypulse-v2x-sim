"""仿真生命周期接口与WebSocket实时推送"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from queue import Empty

from fastapi import APIRouter, Depends, Response, WebSocket, WebSocketDisconnect, status

from ...schemas.events import EventCreatedResponse, EventRequest
from ...schemas.simulations import (
    SimulationStatusResponse,
    StartSimulationRequest,
    StartSimulationResponse,
    StopSimulationResponse,
)
from ...services.simulation_service import SimulationService, TERMINAL_STATES
from ..deps import get_simulation_service

router = APIRouter()
logger = logging.getLogger(__name__)


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
    )


@router.get("/simulations/{session_id}", response_model=SimulationStatusResponse)
def get_simulation_status(
    session_id: str,
    service: SimulationService = Depends(get_simulation_service),
) -> SimulationStatusResponse:
    return SimulationStatusResponse(**service.snapshot(session_id))


@router.post("/simulations/{session_id}/stop", response_model=StopSimulationResponse)
def stop_simulation(
    session_id: str,
    service: SimulationService = Depends(get_simulation_service),
) -> StopSimulationResponse:
    snapshot = service.stop(session_id)
    return StopSimulationResponse(session_id=session_id, state=snapshot.state)


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
    if not app.state.artifacts_ready or not app.state.sumo_home_configured:
        await websocket.close(code=1011)
        return

    service: SimulationService = app.state.simulation_service
    subscription = service.subscribe(session_id)
    logger.info("WebSocket connected for session %s", session_id)

    try:
        initial_snapshot = subscription.get(timeout=2.0)
        await websocket.send_json(
            {
                "type": "snapshot",
                "data": service.serialize_snapshot(initial_snapshot),
            }
        )

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

            await websocket.send_json(
                {
                    "type": "snapshot",
                    "data": service.serialize_snapshot(snapshot),
                }
            )
            if snapshot.state in TERMINAL_STATES:
                break
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected for session %s", session_id)
    except Exception:
        logger.exception("WebSocket stream failed for session %s", session_id)
        await websocket.close(code=1011)
    finally:
        subscription.close()
        logger.info("WebSocket closed for session %s", session_id)
