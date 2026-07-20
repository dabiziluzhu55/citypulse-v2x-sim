"""HTTP/JSON client for an algorithm service outside the SUMO process."""

from __future__ import annotations

import json
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .policy_transport import (
    to_protocol_payload,
    validate_initialize_response,
    validate_step_response,
)


class HttpAlgorithmClient:
    """Call the small initialize/step/finish algorithm contract."""

    def __init__(self, endpoint: str, timeout: float = 2.0) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout = float(timeout)
        if not self.endpoint.startswith(("http://", "https://")):
            raise ValueError("Algorithm endpoint must start with http:// or https://.")
        if self.timeout <= 0:
            raise ValueError("Algorithm HTTP timeout must be positive.")

    def _post(self, path: str, payload: object) -> object:
        request = Request(
            f"{self.endpoint}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                content = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Algorithm service {path} returned HTTP {exc.code}: {detail}"
            ) from exc
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(f"Algorithm service {path} is unavailable: {exc}") from exc
        if not content:
            return {}
        try:
            return json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Algorithm service {path} returned invalid JSON.") from exc

    def initialize(self, metadata) -> None:
        response = self._post("/initialize", to_protocol_payload(metadata))
        validate_initialize_response(
            response,
            episode_id=metadata.episode_id,
            source="Algorithm HTTP /initialize",
        )

    def decide(self, observation):
        response = self._post("/step", to_protocol_payload(observation))
        return validate_step_response(
            response,
            episode_id=observation.episode_id,
            step_id=observation.step_id,
            source="Algorithm HTTP /step",
        )

    def finish(self, payload: Mapping[str, object]) -> None:
        self._post("/finish", dict(payload))
