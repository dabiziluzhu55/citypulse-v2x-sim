"""FastAPI 应用启动入口"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from simulation.sumo import SimulationManager

from .api.router import api_router
from .controllers.runtime import AlgorithmRuntimeStore
from .core.config import get_settings
from .core.exceptions import register_exception_handlers
from .core.sumo_env import configure_sumo_home
from .metrics.session_hub import SessionMetricsHub
from .services.map_service import MapService
from .services.simulation_service import SimulationService
from .services.snapshot_serializer import SnapshotSerializer

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    logger.info("Starting %s", settings.app_name)
    logger.info("Project root: %s", settings.project_root)
    logger.info("Generated directory: %s", settings.generated_dir)
    logger.info("Enabled control modes: %s", list(settings.enabled_control_modes()))

    sumo_home = configure_sumo_home(settings)
    missing_files = settings.missing_generated_files()
    if missing_files:
        logger.warning("Missing generated artifacts: %s", missing_files)

    manager = SimulationManager(
        generated_dir=settings.generated_dir,
        session_root=settings.session_root,
    )
    map_service = MapService(settings, manager)
    serializer = SnapshotSerializer(map_service)
    algorithm_store = AlgorithmRuntimeStore()
    metrics_hub = SessionMetricsHub()
    simulation_service = SimulationService(
        manager=manager,
        serializer=serializer,
        settings=settings,
        algorithm_store=algorithm_store,
        metrics_hub=metrics_hub,
    )

    app.state.settings = settings
    app.state.simulation_manager = manager
    app.state.map_service = map_service
    app.state.snapshot_serializer = serializer
    app.state.algorithm_store = algorithm_store
    app.state.metrics_hub = metrics_hub
    app.state.simulation_service = simulation_service
    app.state.sumo_home_configured = sumo_home is not None
    app.state.artifacts_ready = len(missing_files) == 0
    app.state.missing_files = missing_files
    app.state.simulation_manager_ready = True

    logger.info("SimulationManager initialized.")
    yield

    simulation_service.shutdown_active_session()
    logger.info("Backend shutdown complete.")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
