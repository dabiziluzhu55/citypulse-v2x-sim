"""FastAPI应用启动入口"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from simulation.sumo.engine.distributed import RedisUnavailableError

from .copilot.llm import LLMError, QwenProvider
from .copilot.rag import ChromaKnowledgeRetriever
from .copilot.traffic_tools import RoadTopology, ToolDataUnavailableError
from .api.router import api_router
from .controllers.runtime import AlgorithmRuntimeStore
from .core.config import get_settings
from .core.exceptions import register_exception_handlers
from .core.sumo_env import configure_sumo_home
from .metrics.session_hub import SessionMetricsHub
from .services.manager_factory import create_simulation_manager, probe_redis_manager
from .services.map_service import MapService
from .services.scenario_export_service import ScenarioExportService
from .services.session_metadata import create_session_metadata_store
from .services.simulation_service import (
    SimulationService,
    detect_uvicorn_worker_count,
    recommended_uvicorn_workers,
)
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
    mode = settings.normalized_manager_mode()
    logger.info("Starting %s", settings.app_name)
    logger.info("Project root: %s", settings.project_root)
    logger.info("Generated directory: %s", settings.generated_dir)
    logger.info("Session root: %s", settings.session_root)
    logger.info("Simulation manager mode: %s", mode)
    logger.info("Enabled control modes: %s", list(settings.enabled_control_modes()))
    logger.info("Algorithm base URL: %s", settings.algorithm_base_url)

    worker_count = detect_uvicorn_worker_count()
    algorithm_state_shared = False
    if worker_count is not None and worker_count > 1:
        logger.warning(
            "检测到 Uvicorn/Web worker=%s > 1；AlgorithmRuntimeStore 为进程内状态，"
            "不支持跨进程共享。请使用 --workers %s",
            worker_count,
            recommended_uvicorn_workers(),
        )
    else:
        logger.info(
            "AlgorithmRuntimeStore 为进程内状态；推荐 Uvicorn workers=%s",
            recommended_uvicorn_workers(),
        )

    sumo_home = configure_sumo_home(settings)
    missing_files = settings.missing_generated_files()
    if missing_files:
        logger.warning("Missing generated artifacts: %s", missing_files)

    session_root_ok = settings.session_root.exists() or True
    # session_root允许启动时不存在，首次仿真会创建；但路径父目录应可写
    try:
        settings.session_root.mkdir(parents=True, exist_ok=True)
        session_root_ok = True
    except OSError as exc:
        session_root_ok = False
        logger.error("Cannot prepare session_root %s: %s", settings.session_root, exc)

    redis_ready = True
    redis_error: str | None = None
    manager = None
    manager_ready = False

    if mode == "redis":
        redis_ready, redis_error = probe_redis_manager(settings)
        if not redis_ready:
            logger.error(
                "Redis session store unavailable; refusing to fall back to local. error=%s",
                redis_error,
            )
        else:
            try:
                manager = create_simulation_manager(settings)
                manager_ready = True
            except RedisUnavailableError as exc:
                redis_ready = False
                redis_error = str(exc)
                logger.error("Failed to create RedisSimulationManager: %s", exc)
    else:
        manager = create_simulation_manager(settings)
        manager_ready = True

    metadata_store = create_session_metadata_store(
        mode=mode if redis_ready or mode == "local" else "local",
        redis_url=settings.citypulse_redis_state_url,
        key_prefix=settings.backend_redis_key_prefix,
        terminal_ttl_seconds=settings.citypulse_session_ttl_seconds,
    )
    if mode == "redis" and redis_ready:
        try:
            metadata_store.ping()
        except Exception as exc:
            redis_ready = False
            redis_error = f"Backend metadata Redis ping failed: {exc}"
            manager_ready = False
            logger.error("%s", redis_error)

    map_service = MapService(settings, manager) if manager is not None else None
    serializer = (
        SnapshotSerializer(map_service)
        if map_service is not None
        else SnapshotSerializer(_NullConverter())
    )
    algorithm_store = AlgorithmRuntimeStore()
    metrics_hub = SessionMetricsHub(
        session_root=settings.session_root,
        traffic_manifest_path=settings.generated_dir
        / "manifests"
        / "traffic_manifest.json",
        metadata_store=metadata_store,
    )
    simulation_service = None
    if manager is not None and manager_ready:
        simulation_service = SimulationService(
            manager=manager,
            serializer=serializer,
            settings=settings,
            algorithm_store=algorithm_store,
            metrics_hub=metrics_hub,
            metadata_store=metadata_store,
        )

    scenario_export_service = (
        ScenarioExportService(settings, manager) if manager is not None else None
    )

    copilot_provider = None
    copilot_config_error = None
    try:
        copilot_provider = QwenProvider.from_settings(settings)
    except LLMError as exc:
        copilot_config_error = "Copilot model configuration is invalid."
        logger.error("Copilot provider configuration failed: code=%s", exc.code)

    copilot_topology = None
    topology_path = settings.generated_dir / "manifests" / "tls_manifest.json"
    if topology_path.is_file():
        try:
            copilot_topology = RoadTopology.from_tls_manifest(topology_path)
        except ToolDataUnavailableError as exc:
            logger.warning("Copilot topology is unavailable: %s", exc)

    knowledge_retriever = ChromaKnowledgeRetriever(
        index_dir=settings.rag_index_path,
        knowledge_manifest_path=settings.rag_knowledge_manifest_path,
        embedding_model=settings.rag_embedding_model,
        embedding_model_path=settings.rag_embedding_model_resolved_path,
        device=settings.rag_embedding_device,
        collection_name=settings.rag_collection_name,
        query_instruction=settings.rag_query_instruction,
        query_timeout_seconds=settings.rag_query_timeout_seconds,
    )
    logger.info(
        "Traffic knowledge RAG configured: index=%s model=%s collection=%s",
        settings.rag_index_path,
        settings.rag_embedding_model,
        settings.rag_collection_name,
    )

    app.state.settings = settings
    app.state.simulation_manager = manager
    app.state.map_service = map_service
    app.state.snapshot_serializer = serializer
    app.state.algorithm_store = algorithm_store
    app.state.metrics_hub = metrics_hub
    app.state.session_metadata_store = metadata_store
    app.state.simulation_service = simulation_service
    app.state.scenario_export_service = scenario_export_service
    app.state.copilot_provider = copilot_provider
    app.state.copilot_config_error = copilot_config_error
    app.state.copilot_topology = copilot_topology
    app.state.knowledge_retriever = knowledge_retriever
    if simulation_service is not None:
        simulation_service.configure_ai_control(
            provider=copilot_provider,
            retriever=knowledge_retriever,
            topology=copilot_topology,
        )
        try:
            recovered = simulation_service.recover_sessions()
            if recovered:
                logger.info("Recovered %s session watcher(s) after dependencies loaded", recovered)
        except Exception:
            logger.exception("Session recovery failed during startup")
    app.state.sumo_home_configured = sumo_home is not None
    app.state.artifacts_ready = len(missing_files) == 0
    app.state.missing_files = missing_files
    app.state.session_root_ready = session_root_ok
    app.state.simulation_manager_mode = mode
    app.state.simulation_manager_ready = bool(manager_ready)
    app.state.redis_ready = redis_ready
    app.state.redis_error = redis_error
    app.state.algorithm_state_shared = algorithm_state_shared
    app.state.recommended_uvicorn_workers = recommended_uvicorn_workers()
    app.state.detected_uvicorn_workers = worker_count

    logger.info(
        "Backend ready: mode=%s manager_ready=%s redis_ready=%s sumo_home=%s artifacts=%s",
        mode,
        manager_ready,
        redis_ready,
        sumo_home is not None,
        len(missing_files) == 0,
    )
    yield

    if simulation_service is not None:
        simulation_service.shutdown()
    logger.info("Backend shutdown complete.")


class _NullConverter:
    def xy_to_lonlat(self, x: float, y: float):
        return None, None


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
