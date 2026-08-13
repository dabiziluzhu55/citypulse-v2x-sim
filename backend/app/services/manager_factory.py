"""仿真管理器工厂：按配置选择local SimulationManager或RedisSimulationManager"""

from __future__ import annotations

import logging
from typing import Any

from simulation.sumo import RedisSimulationManager, SimulationManager
from simulation.sumo.engine.distributed import RedisUnavailableError

from ..core.config import Settings

logger = logging.getLogger(__name__)


def create_simulation_manager(settings: Settings) -> Any:
    """根据simulation_manager_mode创建管理器

    redis模式连接失败时抛出RedisUnavailableError，绝不静默降级为local
    """

    mode = settings.simulation_manager_mode
    if mode == "local":
        logger.info(
            "Simulation manager mode=local (in-process SimulationManager)"
        )
        return SimulationManager(
            generated_dir=settings.generated_dir,
            session_root=settings.session_root,
        )

    if mode == "redis":
        logger.info(
            "Simulation manager mode=redis (RedisSimulationManager) "
            "state_url=%s key_prefix=%s session_ttl=%ss",
            settings.citypulse_redis_state_url,
            settings.citypulse_redis_key_prefix,
            settings.citypulse_session_ttl_seconds,
        )
        return RedisSimulationManager(
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
    """探测Redis会话存储是否可用；不创建Celery任务"""

    try:
        from simulation.sumo.engine.distributed.store import RedisSessionStore

        store = RedisSessionStore(
            settings.citypulse_redis_state_url,
            key_prefix=settings.citypulse_redis_key_prefix,
            terminal_ttl_seconds=settings.citypulse_session_ttl_seconds,
        )
        store.ping()
        return True, None
    except Exception as exc:
        return False, str(exc)


__all__ = [
    "RedisUnavailableError",
    "create_simulation_manager",
    "probe_redis_manager",
]
