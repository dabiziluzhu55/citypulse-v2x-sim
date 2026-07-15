"""SUMO环境初始化与sumolib加载"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from .config import Settings

logger = logging.getLogger(__name__)


def configure_sumo_home(settings: Settings) -> Path | None:
    """Ensure SUMO_HOME is configured and sumolib is importable."""
    sumo_home = settings.resolved_sumo_home()
    if sumo_home is None:
        logger.warning("SUMO_HOME is not configured or does not exist.")
        return None

    os.environ.setdefault("SUMO_HOME", str(sumo_home))
    tools_path = sumo_home / "tools"
    if tools_path.is_dir() and str(tools_path) not in sys.path:
        sys.path.insert(0, str(tools_path))
        logger.info("Added SUMO tools to sys.path: %s", tools_path)

    try:
        import sumolib  # noqa: F401
    except ImportError as exc:
        logger.error("Failed to import sumolib from SUMO_HOME=%s: %s", sumo_home, exc)
        return None

    logger.info("SUMO_HOME configured: %s", sumo_home)
    return sumo_home


def import_sumolib():
    import sumolib

    return sumolib
