"""仿真配置与环境设置"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def resolve_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


REQUIRED_GENERATED_FILES = (
    "traffic_manifest.json",
    "tls_manifest.json",
    "TotalMap_20.signals.net.xml",
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

    default_intersection_id: str = "demo_2"
    default_map_radius_meters: float = 600.0
    default_snapshot_interval_seconds: float = 0.2

    mvp_intersection_ids: tuple[str, ...] = ("demo_2",)
    mvp_control_modes: tuple[str, ...] = ("fixed",)

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
    def signals_net_path(self) -> Path:
        return self.generated_dir / "TotalMap_20.signals.net.xml"

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.frontend_origins.split(",") if origin.strip()]

    def resolved_sumo_home(self) -> Path | None:
        import os

        raw = self.sumo_home or os.environ.get("SUMO_HOME")
        if not raw:
            return None
        path = Path(raw).expanduser()
        return path if path.is_dir() else None

    def missing_generated_files(self) -> list[str]:
        missing: list[str] = []
        for name in REQUIRED_GENERATED_FILES:
            path = self.generated_dir / name
            if not path.is_file():
                missing.append(str(path.relative_to(self.project_root)))
        return missing


@lru_cache
def get_settings() -> Settings:
    return Settings()
