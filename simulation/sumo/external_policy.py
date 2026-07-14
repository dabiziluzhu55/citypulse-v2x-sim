"""HTTP/JSON client for an algorithm service outside the SUMO process."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


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
        response = self._post("/initialize", asdict(metadata))
        if not isinstance(response, dict) or response.get("ready") is not True:
            raise RuntimeError("Algorithm /initialize must return {\"ready\": true}.")

    def decide(self, observation) -> Mapping[str, int | None]:
        response = self._post("/step", asdict(observation))
        if not isinstance(response, dict):
            raise TypeError("Algorithm /step response must be a JSON object.")
        if response.get("step_id") != observation.step_id:
            raise ValueError(
                "Algorithm /step must echo the request step_id; "
                f"expected {observation.step_id}, got {response.get('step_id')!r}."
            )
        actions = response.get("actions")
        if not isinstance(actions, dict):
            raise TypeError("Algorithm /step response needs an actions object.")
        return actions

    def finish(self, payload: Mapping[str, object]) -> None:
        self._post("/finish", dict(payload))
