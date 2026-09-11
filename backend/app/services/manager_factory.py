"""仿真管理器工厂：按配置选择 local SimulationManager 或 RedisSimulationManager"""

from __future__ import annotations

import logging
from typing import Any

from ..core.config import Settings

logger = logging.getLogger(__name__)


def create_simulation_manager(settings: Settings) -> Any:
    """根据 simulation_manager_mode 创建管理器

    redis 模式只导入 distributed client，避免加载本地 SUMO kernel。
    local 模式 lazy import SimulationManager。
    """

    mode = settings.simulation_manager_mode
    if mode == "local":
        from simulation.sumo.engine.session import SimulationManager

        logger.info(
            "Simulation manager mode=local (in-process SimulationManager)"
        )
        return SimulationManager(
            generated_dir=settings.generated_dir,
            session_root=settings.session_root,
        )

    if mode == "redis":
        from simulation_protocol.client import RedisSimulationClient

        logger.info(
            "Simulation manager mode=redis (RedisSimulationClient) "
            "state_url=%s key_prefix=%s session_ttl=%ss",
            settings.citypulse_redis_state_url,
            settings.citypulse_redis_key_prefix,
            settings.citypulse_session_ttl_seconds,
        )
        return RedisSimulationClient(
            redis_url=settings.citypulse_redis_state_url,
            generated_dir=settings.generated_dir,
            session_root=settings.session_root,
            terminal_ttl_seconds=settings.citypulse_session_ttl_seconds,
            command_timeout_seconds=settings.citypulse_command_timeout_seconds,
            heartbeat_ttl_seconds=settings.citypulse_worker_heartbeat_ttl_seconds,
        )

    raise ValueError(
        f"Unsupported simulation_manager_mode={mode!r}; expected 'local' or 'redis'."
    )


def probe_redis_manager(settings: Settings) -> tuple[bool, str | None]:
    """探测 Redis 会话存储是否可用；不创建 Celery 任务"""

    try:
        from simulation_protocol.store import RedisSessionStore

        store = RedisSessionStore(
            settings.citypulse_redis_state_url,
            key_prefix=settings.citypulse_redis_key_prefix,
            terminal_ttl_seconds=settings.citypulse_session_ttl_seconds,
        )
        store.ping()
        return True, None
    except Exception as exc:
        return False, str(exc)


def redis_unavailable_error_type():
    from simulation_protocol.exceptions import RedisUnavailableError

    return RedisUnavailableError


__all__ = [
    "create_simulation_manager",
    "probe_redis_manager",
    "redis_unavailable_error_type",
]
