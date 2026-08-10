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
    # STGCN模型包目录（含stgcn_best.pt等）；空则仅moving_average
    prediction_model_dir: str = ""
    # 外部STGCN参考实现根目录（含model/models.py）；空则尝试从PYTHONPATH导入
    stgcn_root: str = ""

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
