"""Runtime dispatcher for versioned CoV2X deployment candidates."""

from __future__ import annotations

from importlib import import_module
import os
from types import ModuleType
from typing import Any, Mapping

from .aliases import (
    DEFAULT_MODEL_ALIAS,
    resolve_model,
    resolve_model_path,
    validate_alias_combo,
)

_active_adapter: ModuleType | None = None
_environment_before: dict[str, str] | None = None
_event_sink: Any | None = None


def _selected_alias() -> str:
    return os.environ.get(
        "COV2X_MODEL_ALIAS", DEFAULT_MODEL_ALIAS
    ).strip()


def _capture_environment() -> dict[str, str]:
    return {
        name: value
        for name, value in os.environ.items()
        if name.startswith("COV2X_")
    }


def _restore_environment(snapshot: Mapping[str, str]) -> None:
    for name in tuple(os.environ):
        if name.startswith("COV2X_"):
            os.environ.pop(name, None)
    os.environ.update(snapshot)


def set_v2x_event_sink(sink: Any | None) -> None:
    """Configure an optional in-process consumer for V2X event records."""
    global _event_sink
    if sink is not None and not callable(sink) and not callable(
        getattr(sink, "emit", None)
    ):
        raise TypeError("V2X event sink must be callable or provide emit()")
    _event_sink = sink
    if _active_adapter is not None:
        setter = getattr(_active_adapter, "set_v2x_event_sink", None)
        if setter is None:
            if sink is not None:
                raise RuntimeError(
                    "active CoV2X candidate does not export V2X events"
                )
        else:
            setter(sink)


def drain_v2x_events() -> dict[str, Any]:
    """Drain unread V2X events for the active deployment candidate."""
    if _active_adapter is None:
        raise RuntimeError("CoV2X candidate is not initialized")
    drain = getattr(_active_adapter, "drain_v2x_events", None)
    if drain is None:
        raise RuntimeError("active CoV2X candidate has no V2X event drain")
    return drain()
def initialize(payload: Mapping[str, Any]) -> dict[str, Any]:
    global _active_adapter, _environment_before
    if _active_adapter is not None:
        raise RuntimeError("CoV2X candidate is already initialized")

    before = _capture_environment()
    alias = _selected_alias()
    model = resolve_model(alias)
    resolve_model_path(alias)
    validate_alias_combo(
        (payload.get("intersections", {}) or {}).keys(),
        alias,
    )
    adapter = import_module(model.adapter_module)
    configured = False
    try:
        adapter.configure(model)
        configured = True
        setter = getattr(adapter, "set_v2x_event_sink", None)
        if setter is not None:
            setter(_event_sink)
        elif _event_sink is not None:
            raise RuntimeError(
                "selected CoV2X candidate does not export V2X events"
            )
        response = adapter.initialize(payload)
    except Exception:
        if configured:
            reset = getattr(adapter, "reset", None)
            if reset is not None:
                reset()
        _restore_environment(before)
        raise

    _environment_before = before
    _active_adapter = adapter
    return response


def step(payload: Mapping[str, Any]) -> dict[str, Any]:
    if _active_adapter is None:
        raise RuntimeError("CoV2X candidate is not initialized")
    return _active_adapter.step(payload)


def finish(payload: Mapping[str, Any]) -> Any:
    global _active_adapter, _environment_before
    if _active_adapter is None or _environment_before is None:
        raise RuntimeError("CoV2X candidate is not initialized")
    adapter = _active_adapter
    before = _environment_before
    try:
        return adapter.finish(payload)
    finally:
        _active_adapter = None
        _environment_before = None
        _restore_environment(before)
