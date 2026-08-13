"""Select and exclusively own the in-process SUMO runtime."""

from __future__ import annotations

import importlib
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Sequence


LOGGER = logging.getLogger(__name__)
_RUNTIME_LOCK = threading.Lock()


class SumoRuntimeError(RuntimeError):
    """Raised when the requested SUMO runtime cannot be loaded or started."""


class SumoRuntimeBusyError(SumoRuntimeError):
    """Raised when another SUMO runtime already owns this Python process."""


class SumoRuntime:
    """Proxy a libsumo or TraCI module behind one process-wide lifecycle lease."""

    def __init__(self, sumolib, api, *, backend: str, binary_name: str) -> None:
        self.sumolib = sumolib
        self._api = api
        self.backend = backend
        self.binary_name = binary_name
        self._owns_lock = False
        self._started = False

    def __getattr__(self, name: str):
        return getattr(self._api, name)

    @property
    def started(self) -> bool:
        """Return whether this adapter successfully started its SUMO runtime."""

        return self._started

    def command(self, arguments: Sequence[object]) -> list[str]:
        return [
            str(self.sumolib.checkBinary(self.binary_name)),
            *(str(value) for value in arguments),
        ]

    def start(self, command: Sequence[str]) -> None:
        if self._started or self._owns_lock:
            raise SumoRuntimeError("This SUMO runtime has already been started.")
        if not _RUNTIME_LOCK.acquire(blocking=False):
            raise SumoRuntimeBusyError(
                "Another SUMO runtime is already active in this Python process. "
                "Use Redis/Celery prefork workers for concurrent simulations."
            )
        self._owns_lock = True
        LOGGER.info("Starting SUMO with %s (%s)", self.backend, command[0])
        try:
            self._api.start(list(command))
            self._started = True
        except BaseException:
            try:
                self._api.close()
            except BaseException:
                pass
            self._release()
            raise

    def close(self) -> None:
        try:
            if self._started:
                self._api.close()
        finally:
            self._started = False
            self._release()

    def _release(self) -> None:
        if self._owns_lock:
            self._owns_lock = False
            _RUNTIME_LOCK.release()


def _normalized_path(path: str) -> str:
    return os.path.normpath(path)


def _sumo_tools_paths() -> list[str]:
    sumo_home = os.environ.get("SUMO_HOME")
    if not sumo_home:
        return []
    return [str(Path(sumo_home) / "tools")]


def _ensure_sumo_tools_on_path() -> None:
    for tools_path in _sumo_tools_paths():
        if tools_path not in sys.path:
            sys.path.append(tools_path)


def _import_module_avoiding_sumo_tools(module_name: str):
    """Import pip libsumo even when PYTHONPATH points at apt SUMO tools stubs."""

    blocked = {_normalized_path(path) for path in _sumo_tools_paths()}
    if not blocked:
        return importlib.import_module(module_name)

    saved_path = sys.path[:]
    sys.path = [
        entry for entry in sys.path if _normalized_path(entry) not in blocked
    ]
    try:
        return importlib.import_module(module_name)
    finally:
        sys.path = saved_path


def load_sumo_runtime(*, gui: bool) -> SumoRuntime:
    """Load strict headless libsumo or the explicit TraCI GUI debug path."""

    _ensure_sumo_tools_on_path()

    try:
        sumolib = importlib.import_module("sumolib")
    except ImportError as exc:
        raise SumoRuntimeError(
            "Cannot import sumolib. Set SUMO_HOME to the SUMO installation and "
            "verify it with: python -c \"import sumolib\""
        ) from exc

    module_name = "traci" if gui else "libsumo"
    try:
        if gui:
            api = importlib.import_module(module_name)
        else:
            api = _import_module_avoiding_sumo_tools(module_name)
    except ImportError as exc:
        if gui:
            message = (
                "Cannot import traci for SUMO GUI debugging. Verify the binding with: "
                "python -c \"import traci\""
            )
        else:
            message = (
                "Cannot import libsumo for headless simulation. Install the SUMO "
                "libsumo Python binding and verify it with: "
                "python -c \"import libsumo; import sumolib\". Headless sessions do "
                "not fall back to TraCI."
            )
        raise SumoRuntimeError(message) from exc

    return SumoRuntime(
        sumolib,
        api,
        backend="traci-gui" if gui else "libsumo",
        binary_name="sumo-gui" if gui else "sumo",
    )
