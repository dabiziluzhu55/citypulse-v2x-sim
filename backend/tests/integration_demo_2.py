"""SUMO端到端集成测试

在仓库根目录显式执行：

    python backend/tests/integration_demo_2.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASE_URL = "http://localhost:8000/api/v1"


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        health = (await client.get("/health")).json()
        if health.get("status") != "ok":
            raise RuntimeError(f"Backend is not ready: {health}")

        catalog = (await client.get("/catalog")).json()
        demo = next(item for item in catalog["intersections"] if item["intersection_id"] == "demo_2")

        start_payload = {
            "intersection_ids": ["demo_2"],
            "period": "morning_peak",
            "origins": {},
            "window_start_seconds": 0,
            "duration_seconds": 120,
            "flow_multiplier": 1.2,
            "control_mode": "fixed",
            "seed": 42,
            "step_length": 0.05,
            "realtime": True,
            "gui": False,
            "snapshot_interval_seconds": 0.2,
            "initial_events": [],
        }
        created = (await client.post("/simulations", json=start_payload)).json()
        session_id = created["session_id"]

        vehicle_seen = False
        async with client.stream("GET", f"/simulations/{session_id}") as _:
            pass

        import websockets

        ws_url = f"ws://localhost:8000{created['websocket_url']}"
        async with websockets.connect(ws_url) as websocket:
            while True:
                message = json.loads(await websocket.recv())
                if message["type"] != "snapshot":
                    continue
                vehicles = message["data"]["vehicles"]
                if vehicles:
                    vehicle = vehicles[0]
                    assert "longitude" in vehicle
                    assert "latitude" in vehicle
                    vehicle_seen = True
                    break
                if message["data"]["state"] in {"STOPPED", "COMPLETED", "FAILED"}:
                    break

        await client.post(f"/simulations/{session_id}/stop")
        if not vehicle_seen:
            raise RuntimeError("No vehicle snapshot received during integration test.")
        print("Integration test passed for demo_2.")


if __name__ == "__main__":
    asyncio.run(main())
