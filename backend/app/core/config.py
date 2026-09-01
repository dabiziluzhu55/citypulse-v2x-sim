# 仿真配置与环境设置

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def resolve_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


REQUIRED_GENERATED_FILES = (
    "manifests/traffic_manifest.json",
    "manifests/tls_manifest.json",
    "network/TotalMap_20.signals.net.xml",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(resolve_project_root() / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "CityPulse-V2X Backend"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_debug: bool = False

    frontend_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    sumo_home: str | None = None
    sumo_generated_dir: str = "data/maps/sumo/generated"
    sumo_session_root: str = "outputs/sessions"
    sumo_scenario_export_dir: str = "outputs/scenario_exports"

    # local=进程内SimulationManager；redis=RedisSimulationManager多会话并发
    simulation_manager_mode: str = "local"

    # 与SUMO worker共享的Redis会话状态（仅redis模式）
    citypulse_redis_state_url: str = "redis://127.0.0.1:6380/1"
    citypulse_redis_key_prefix: str = "citypulse"
    citypulse_session_ttl_seconds: int = 86400
    citypulse_command_timeout_seconds: float = 30.0
    citypulse_worker_heartbeat_ttl_seconds: int = 15

    default_intersection_id: str = "demo_2"
    default_map_radius_meters: float = 600.0
    default_snapshot_interval_seconds: float = 0.5

    mvp_intersection_ids: tuple[str, ...] = ("demo_2",)

    # 启用模式白名单（逗号分隔）；必须是registry子集空字符串表示启用注册表全部模式
    enabled_control_modes_csv: str = ""

    # SUMO worker回调backend内部算法协议的可达基址（多机部署时必须改成外部可达URL）
    algorithm_base_url: str = "http://127.0.0.1:8000"
    algorithm_timeout: float = 2.0
    decision_interval: float = 5.0

    # 事件识别与短时预测采样
    intelligence_sample_seconds: float = 5.0
    intelligence_history_frames: int = 12
    prediction_horizon_seconds: float = 60.0
    # NarrowNet-TDP运行时包；相对路径相对仓库根；空字符串关闭模型仅moving_average
    prediction_model_dir: str = "backend/models/prediction/narrow_net_tdp"
    # 已废弃保留兼容旧环境变量NarrowNet推理不再依赖外部STGCN仓库
    stgcn_root: str = ""

    # Traffic Copilot：Qwen 服务只提供模型推理，默认通过本机回环/SSH 隧道访问
    citypulse_qwen_base_url: str = "http://127.0.0.1:18000/v1"
    citypulse_qwen_model: str = "Qwen/Qwen2.5-7B-Instruct"
    citypulse_qwen_api_key: str | None = None
    citypulse_qwen_timeout_seconds: float = 60.0
    citypulse_qwen_temperature: float = 0.2
    citypulse_qwen_max_tokens: int = 512
    copilot_max_rounds: int = 4
    copilot_max_tool_calls: int = 8
    copilot_max_tool_result_chars: int = 20_000

    # Copilot 历史查询边界；历史采样复用 intelligence_sample_seconds
    history_default_lookback_seconds: float = 300.0
    history_max_query_seconds: float = 3600.0
    history_max_points: int = 120

    # Traffic knowledge RAG；索引由 scripts/rag/build_knowledge_index.py 离线构建
    rag_index_dir: str = "outputs/rag/chroma"
    rag_knowledge_manifest: str = "traffic_knowledge/manifest.json"
    rag_embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    rag_embedding_model_path: str = ""
    rag_embedding_device: str = "auto"
    rag_collection_name: str = "citypulse_traffic_knowledge"
    rag_query_instruction: str = (
        "Given a traffic control or traffic engineering question, retrieve "
        "relevant passages that help answer the question."
    )
    rag_top_k: int = 5
    rag_query_timeout_seconds: float = 30.0

    # Event-scoped Qwen signal control.  These values are serialized into the
    # SUMO session policy; the feature remains opt-in per disturbance event.
    ai_control_plan_valid_seconds: float = 30.0
    ai_control_slot_seconds: float = 5.0
    ai_control_replan_seconds: float = 30.0
    ai_control_recovery_seconds: float = 60.0
    ai_control_recovery_clear_samples: int = 3
    ai_control_scope_hops: int = 1
    ai_control_max_plan_failures: int = 2

    cesium_ion_token: str | None = None
    tianditu_token: str | None = None

    @property
    def project_root(self) -> Path:
        return resolve_project_root()

    @property
    def generated_dir(self) -> Path:
        return self.project_root / self.sumo_generated_dir

    @property
    def session_root(self) -> Path:
        return self.project_root / self.sumo_session_root

    @property
    def scenario_export_root(self) -> Path:
        return self.project_root / self.sumo_scenario_export_dir

    @property
    def rag_index_path(self) -> Path:
        path = Path(self.rag_index_dir).expanduser()
        return path if path.is_absolute() else self.project_root / path

    @property
    def rag_knowledge_manifest_path(self) -> Path:
        path = Path(self.rag_knowledge_manifest).expanduser()
        return path if path.is_absolute() else self.project_root / path

    @property
    def rag_embedding_model_resolved_path(self) -> Path | None:
        raw = self.rag_embedding_model_path.strip()
        if not raw:
            return None
        path = Path(raw).expanduser()
        return path if path.is_absolute() else self.project_root / path

    @property
    def prediction_model_path(self) -> Path | None:
        raw = self.prediction_model_dir.strip()
        if not raw:
            return None
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = self.project_root / path
        return path

    @property
    def ai_control_config(self):
        from simulation.sumo.engine.ai_control import AIControlConfig

        return AIControlConfig(
            plan_valid_seconds=self.ai_control_plan_valid_seconds,
            slot_seconds=self.ai_control_slot_seconds,
            replan_seconds=self.ai_control_replan_seconds,
            recovery_seconds=self.ai_control_recovery_seconds,
            recovery_clear_samples=self.ai_control_recovery_clear_samples,
            scope_hops=self.ai_control_scope_hops,
            max_plan_failures=self.ai_control_max_plan_failures,
        )

    @property
    def signals_net_path(self) -> Path:
        return self.generated_dir / "network" / "TotalMap_20.signals.net.xml"

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.frontend_origins.split(",") if origin.strip()]

    @property
    def backend_redis_key_prefix(self) -> str:
        """Backend元数据独立命名空间，避免与simulation Redis key冲突"""
        return f"{self.citypulse_redis_key_prefix.rstrip(':')}:backend"

    @property
    def is_redis_mode(self) -> bool:
        return self.simulation_manager_mode.strip().lower() == "redis"

    @property
    def is_local_mode(self) -> bool:
        return self.simulation_manager_mode.strip().lower() == "local"

    def normalized_manager_mode(self) -> str:
        mode = self.simulation_manager_mode.strip().lower()
        if mode not in {"local", "redis"}:
            raise ValueError(
                f"simulation_manager_mode must be 'local' or 'redis', got {mode!r}"
            )
        return mode

    def enabled_control_modes(self) -> tuple[str, ...]:
        from ..controllers.registry import list_control_modes, validate_enabled_modes

        raw = self.enabled_control_modes_csv.strip()
        if not raw:
            return tuple(list_control_modes())
        modes = [item.strip() for item in raw.split(",") if item.strip()]
        return validate_enabled_modes(modes)

    def resolved_sumo_home(self) -> Path | None:
        import os
        import site

        raw = self.sumo_home or os.environ.get("SUMO_HOME")
        if raw:
            path = Path(raw).expanduser()
            if path.is_dir():
                return path

        candidates = [Path(root) / "sumo" for root in site.getsitepackages()]
        user_site = site.getusersitepackages()
        if user_site:
            candidates.append(Path(user_site) / "sumo")
        return next(
            (
                path
                for path in candidates
                if (path / "tools" / "sumolib").is_dir()
                and (path / "bin" / ("sumo.exe" if os.name == "nt" else "sumo")).is_file()
            ),
            None,
        )

    def missing_generated_files(self) -> list[str]:
        missing: list[str] = []
        for name in REQUIRED_GENERATED_FILES:
            path = self.generated_dir / name
            if not path.is_file():
                missing.append(str(path.relative_to(self.project_root)))
        return missing


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    # 启动时校验白名单与运行模式合法性
    settings.enabled_control_modes()
    settings.normalized_manager_mode()
    return settings
