"""HTTP/JSON bridge for an algorithm running outside the SUMO process."""

from __future__ import annotations

import json
from dataclasses import asdict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .policy import ControlAction, VehicleAdvice


class HttpControlPolicy:
    """Exchange public policy dataclasses with a remote HTTP service."""

    def __init__(self, endpoint: str, timeout: float = 2.0) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout = float(timeout)
        if not self.endpoint.startswith(("http://", "https://")):
            raise ValueError("Policy endpoint must start with http:// or https://.")
        if self.timeout <= 0:
            raise ValueError("Policy HTTP timeout must be positive.")

    def _post(self, path: str, payload) -> object:
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
                f"Policy service {path} returned HTTP {exc.code}: {detail}"
            ) from exc
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(f"Policy service {path} is unavailable: {exc}") from exc
        if not content:
            return {}
        try:
            return json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Policy service {path} returned invalid JSON.") from exc

    def reset(self, metadata) -> None:
        self._post("/reset", asdict(metadata))

    def act(self, observation) -> ControlAction:
        raw = self._post("/act", asdict(observation))
        if not isinstance(raw, dict):
            raise TypeError("Policy /act response must be a JSON object.")
        phases = raw.get("signal_phases", {})
        raw_advisories = raw.get("vehicle_advisories", {})
        if not isinstance(raw_advisories, dict):
            raise TypeError("vehicle_advisories must be a JSON object.")
        advisories = {}
        allowed_fields = {"target_speed", "lane_index", "duration"}
        for vehicle_id, item in raw_advisories.items():
            if not isinstance(item, dict):
                raise TypeError(f"Advice for {vehicle_id} must be a JSON object.")
            unknown = set(item) - allowed_fields
            if unknown:
                raise ValueError(
                    f"Advice for {vehicle_id} has unknown fields: {sorted(unknown)}"
                )
            advisories[str(vehicle_id)] = VehicleAdvice(**item)
        return ControlAction(signal_phases=phases, vehicle_advisories=advisories)

    def close(self) -> None:
        try:
            self._post("/close", {})
        except RuntimeError:
            pass
