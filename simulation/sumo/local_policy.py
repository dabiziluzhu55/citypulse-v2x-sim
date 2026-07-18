"""In-process algorithm transport with the same dictionary contract as HTTP."""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Mapping

from .policy_transport import (
    to_protocol_payload,
    validate_initialize_response,
    validate_step_response,
)


class LocalAlgorithmClient:
    def __init__(self, module_name: str, module: ModuleType | object | None = None) -> None:
        self.module_name = str(module_name).strip()
        if not self.module_name:
            raise ValueError("Local algorithm module name is required.")
        try:
            self.module = module or importlib.import_module(self.module_name)
        except Exception as exc:
            raise RuntimeError(
                f"Cannot import local algorithm module {self.module_name!r}: {exc}"
            ) from exc
        for name in ("initialize", "step", "finish"):
            if not callable(getattr(self.module, name, None)):
                raise TypeError(
                    f"Local algorithm module {self.module_name!r} needs callable {name}()."
                )

    def initialize(self, metadata) -> None:
        payload = to_protocol_payload(metadata)
        response = to_protocol_payload(self._call("initialize", payload))
        validate_initialize_response(
            response,
            episode_id=metadata.episode_id,
            source=f"Local algorithm {self.module_name}",
        )

    def decide(self, observation):
        payload = to_protocol_payload(observation)
        response = to_protocol_payload(self._call("step", payload))
        return validate_step_response(
            response,
            episode_id=observation.episode_id,
            step_id=observation.step_id,
            source=f"Local algorithm {self.module_name}",
        )

    def finish(self, payload: Mapping[str, object]) -> None:
        self._call("finish", dict(payload))

    def _call(self, name: str, payload: dict) -> object:
        try:
            return getattr(self.module, name)(payload)
        except Exception as exc:
            raise RuntimeError(
                f"Local algorithm {self.module_name}.{name} failed: {exc}"
            ) from exc
