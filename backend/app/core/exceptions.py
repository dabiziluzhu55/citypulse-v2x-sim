"""应用级异常与FastAPI异常处理"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from simulation.sumo.events import EventValidationError
from simulation.sumo.scenario import ScenarioCompilationError
from simulation.sumo.session import SessionBusyError, SessionError, UnknownSessionError

logger = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class ArtifactsNotReadyError(AppError):
    def __init__(self, missing_files: list[str]) -> None:
        super().__init__(
            code="ARTIFACTS_NOT_READY",
            message="Required SUMO generated artifacts are missing.",
            status_code=503,
        )
        self.missing_files = missing_files


class SumoHomeUnavailableError(AppError):
    def __init__(self) -> None:
        super().__init__(
            code="SUMO_HOME_UNAVAILABLE",
            message="SUMO_HOME is not configured or sumolib is unavailable.",
            status_code=503,
        )


def error_payload(code: str, message: str, **extra: Any) -> dict[str, Any]:
    detail: dict[str, Any] = {"code": code, "message": message}
    detail.update(extra)
    return {"detail": detail}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        payload = error_payload(exc.code, exc.message)
        if isinstance(exc, ArtifactsNotReadyError):
            payload["detail"]["missing_files"] = exc.missing_files
        return JSONResponse(status_code=exc.status_code, content=payload)

    @app.exception_handler(SessionBusyError)
    async def handle_session_busy(_: Request, exc: SessionBusyError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=error_payload("SIMULATION_BUSY", str(exc)),
        )

    @app.exception_handler(UnknownSessionError)
    async def handle_unknown_session(_: Request, exc: UnknownSessionError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=error_payload("UNKNOWN_SESSION", str(exc)),
        )

    @app.exception_handler(ScenarioCompilationError)
    async def handle_scenario_error(_: Request, exc: ScenarioCompilationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_payload("SCENARIO_VALIDATION_ERROR", str(exc)),
        )

    @app.exception_handler(EventValidationError)
    async def handle_event_error(_: Request, exc: EventValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_payload("EVENT_VALIDATION_ERROR", str(exc)),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        message = "; ".join(
            f"{'.'.join(str(part) for part in error.get('loc', ()))}: {error.get('msg')}"
            for error in exc.errors()
        )
        return JSONResponse(
            status_code=422,
            content=error_payload("REQUEST_VALIDATION_ERROR", message or "Invalid request."),
        )

    @app.exception_handler(ValidationError)
    async def handle_validation_error(_: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_payload("VALIDATION_ERROR", str(exc)),
        )

    @app.exception_handler(SessionError)
    async def handle_session_error(_: Request, exc: SessionError) -> JSONResponse:
        logger.exception("Session error: %s", exc)
        return JSONResponse(
            status_code=400,
            content=error_payload("SESSION_ERROR", str(exc)),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled server error: %s", exc)
        return JSONResponse(
            status_code=500,
            content=error_payload("INTERNAL_SERVER_ERROR", "An unexpected server error occurred."),
        )
