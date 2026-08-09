"""Non-blocking latest-frame delivery to a trusted in-process AI module."""

from __future__ import annotations

import importlib
import queue
import threading
from types import ModuleType
from typing import Mapping

from .policy_transport import to_protocol_payload


class SimulationTimeFrameClock:
    """Allocate frame IDs at a fixed simulation-time interval."""

    def __init__(self, interval_seconds: float) -> None:
        self.interval_seconds = float(interval_seconds)
        if self.interval_seconds <= 0:
            raise ValueError("AI frame interval must be positive.")
        self.next_time = self.interval_seconds
        self.next_frame_id = 0
        self._last_time = 0.0

    def poll(self, simulation_time: float) -> int | None:
        current = float(simulation_time)
        if current + 1e-9 < self._last_time:
            raise ValueError("Simulation time cannot move backwards.")
        self._last_time = current
        if current + 1e-9 < self.next_time:
            return None
        frame_id = self.reserve()
        while self.next_time <= current + 1e-9:
            self.next_time += self.interval_seconds
        return frame_id

    def reserve(self) -> int:
        frame_id = self.next_frame_id
        self.next_frame_id += 1
        return frame_id


class LocalAIObserver:
    def __init__(self, module_name: str, module: ModuleType | object | None = None) -> None:
        self.module_name = str(module_name).strip()
        if not self.module_name:
            raise ValueError("AI observer module name is required.")
        try:
            self.module = module or importlib.import_module(self.module_name)
        except Exception as exc:
            raise RuntimeError(
                f"Cannot import AI observer module {self.module_name!r}: {exc}"
            ) from exc
        for name in ("initialize", "on_frame", "finish"):
            if not callable(getattr(self.module, name, None)):
                raise TypeError(
                    f"AI observer module {self.module_name!r} needs callable {name}()."
                )
        self._frames: queue.Queue[dict] = queue.Queue(maxsize=1)
        self._closing = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None
        self._error_operation = "on_frame"
        self._summary: dict | None = None
        self._finish_lock = threading.Lock()
        self._finished = False
        self.generated_frames = 0
        self.consumed_frames = 0
        self.dropped_frames = 0

    def initialize(self, metadata) -> None:
        try:
            self.module.initialize(to_protocol_payload(metadata))
        except Exception as exc:
            raise RuntimeError(
                f"AI observer {self.module_name}.initialize failed: {exc}"
            ) from exc
        self._thread = threading.Thread(
            target=self._run,
            name=f"ai-observer-{self.module_name}",
            daemon=True,
        )
        self._thread.start()

    def publish(self, frame) -> None:
        self.check_error()
        payload = to_protocol_payload(frame)
        self.generated_frames += 1
        try:
            self._frames.put_nowait(payload)
            return
        except queue.Full:
            pass
        try:
            self._frames.get_nowait()
            self.dropped_frames += 1
        except queue.Empty:
            pass
        self._frames.put_nowait(payload)

    def check_error(self) -> None:
        if self._error is not None:
            raise RuntimeError(
                f"AI observer {self.module_name}.{self._error_operation} failed: "
                f"{self._error}"
            ) from self._error

    def close(self, summary: Mapping[str, object], timeout: float) -> None:
        if self._thread is None:
            return
        self._summary = dict(summary)
        self._closing.set()
        self._thread.join(timeout=float(timeout))
        if self._thread.is_alive():
            raise TimeoutError(
                f"AI observer {self.module_name} did not stop within {timeout:g}s."
            )
        self._call_finish()
        self.check_error()

    def _run(self) -> None:
        try:
            while not self._closing.is_set() or not self._frames.empty():
                try:
                    payload = self._frames.get(timeout=0.05)
                except queue.Empty:
                    continue
                try:
                    self.module.on_frame(payload)
                    self.consumed_frames += 1
                except BaseException as exc:
                    self._error_operation = "on_frame"
                    self._error = exc
                    break
        finally:
            if self._summary is not None:
                self._call_finish()

    def _call_finish(self) -> None:
        with self._finish_lock:
            if self._finished or self._summary is None:
                return
            payload = dict(self._summary)
            payload["observer_frames"] = {
                "generated": self.generated_frames,
                "consumed": self.consumed_frames,
                "dropped": self.dropped_frames,
            }
            try:
                self.module.finish(payload)
            except BaseException as exc:
                if self._error is None:
                    self._error_operation = "finish"
                    self._error = exc
            self._finished = True
