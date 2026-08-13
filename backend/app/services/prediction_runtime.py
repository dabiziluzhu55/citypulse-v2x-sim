# NarrowNet-TDP在线短时预测运行时加载交付包不可用时由调用方降级moving_average

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_FEATURES = (
    "vehicle_count",
    "halting_count",
    "mean_speed",
    "occupancy",
)

REQUIRED_BUNDLE_FILES = (
    "best.pt",
    "config.json",
    "model_manifest.json",
    "dynamic_candidate_edges.npz",
    "directional_lane_graph_hop075.npz",
)


@dataclass(frozen=True)
class PredictionRuntimeStatus:
    available: bool
    model: str
    model_version: str
    reason: str = ""


class PredictionRuntime:
    """进程内NarrowNet-TDP推理加载失败时available=False不抛到快照路径"""

    def __init__(
        self,
        model_dir: Path | None,
        *,
        device: str | None = None,
    ) -> None:
        self.model_dir = Path(model_dir).expanduser() if model_dir else None
        self._lock = threading.Lock()
        self._torch = None
        self._model = None
        self._device = None
        self._nodes: tuple[str, ...] = ()
        self._features: tuple[str, ...] = DEFAULT_FEATURES
        self._n_his = 12
        self._feature_mean = np.zeros((len(DEFAULT_FEATURES), 1, 1), dtype=np.float32)
        self._feature_std = np.ones((len(DEFAULT_FEATURES), 1, 1), dtype=np.float32)
        self._target_mean = 0.0
        self._target_std = 1.0
        self._model_name = "NarrowNet-TDP"
        self._model_version = ""
        self._available = False
        self._reason = "model_dir_unset"
        if self.model_dir is None:
            return
        self._load(device=device)

    @classmethod
    def from_settings(
        cls,
        *,
        model_dir: str | Path | None,
        device: str | None = None,
        stgcn_root: str | Path | None = None,
        project_root: str | Path | None = None,
    ) -> "PredictionRuntime":
        # stgcn_root保留入参兼容旧调用方NarrowNet不再依赖外部STGCN仓库
        _ = stgcn_root
        if model_dir is None or str(model_dir).strip() == "":
            return cls(None, device=device)
        path = Path(model_dir).expanduser()
        if not path.is_absolute():
            root = Path(project_root) if project_root else Path.cwd()
            path = root / path
        return cls(path.resolve(), device=device)

    @property
    def status(self) -> PredictionRuntimeStatus:
        return PredictionRuntimeStatus(
            available=self._available,
            model=self._model_name,
            model_version=self._model_version,
            reason=self._reason,
        )

    @property
    def nodes(self) -> tuple[str, ...]:
        return self._nodes

    @property
    def features(self) -> tuple[str, ...]:
        return self._features

    @property
    def n_his(self) -> int:
        return self._n_his

    def _load(self, *, device: str | None) -> None:
        assert self.model_dir is not None
        required = {name: self.model_dir / name for name in REQUIRED_BUNDLE_FILES}
        missing = [name for name, path in required.items() if not path.is_file()]
        if missing:
            self._available = False
            self._reason = f"missing_files:{','.join(missing)}"
            logger.warning(
                "NarrowNet-TDP模型包不完整(%s): %s", self.model_dir, self._reason
            )
            return
        try:
            import torch
        except Exception as exc:  # noqa: BLE001
            self._available = False
            self._reason = f"torch_unavailable:{exc}"
            logger.warning("NarrowNet-TDP需要torch当前不可用: %s", exc)
            return

        try:
            from .narrow_net_tdp import (
                StaticDirectionalLaneSTGCN,
                load_directional_lane_graph,
                load_sparse_candidate_graph,
            )

            config = json.loads(required["config.json"].read_text(encoding="utf-8"))
            manifest = json.loads(
                required["model_manifest.json"].read_text(encoding="utf-8")
            )
            nodes = tuple(
                str(item)
                for item in (
                    config.get("node_order")
                    or manifest.get("node_order")
                    or ()
                )
            )
            if len(nodes) != 206:
                raise ValueError(f"expected 206 lane nodes, got {len(nodes)}")
            features = tuple(
                str(item)
                for item in (
                    config.get("features")
                    or manifest.get("features")
                    or DEFAULT_FEATURES
                )
            )
            if tuple(features) != DEFAULT_FEATURES:
                raise ValueError(f"unexpected features: {features}")
            normalization = (
                config.get("normalization")
                or manifest.get("normalization")
                or {}
            )
            feature_mean = np.asarray(
                normalization["feature_mean"], dtype=np.float32
            ).reshape(len(features), 1, 1)
            feature_std = np.asarray(
                normalization["feature_std"], dtype=np.float32
            ).reshape(len(features), 1, 1)
            feature_std = np.where(np.abs(feature_std) < 1e-6, 1.0, feature_std)
            target_mean = float(normalization["target_mean"])
            target_std = float(normalization["target_std"])
            if abs(target_std) < 1e-6:
                target_std = 1.0
            n_his = int(
                config.get("n_his")
                or manifest.get("history_steps")
                or 12
            )
            if n_his != 12:
                raise ValueError(f"NarrowNet-TDP requires n_his=12, got {n_his}")

            sparse_graph = load_sparse_candidate_graph(
                required["dynamic_candidate_edges.npz"]
            )
            directional_graph = load_directional_lane_graph(
                required["directional_lane_graph_hop075.npz"]
            )
            if tuple(sparse_graph["nodes"]) != nodes:
                raise ValueError("candidate graph node_order differs from config")
            if tuple(directional_graph["nodes"]) != nodes:
                raise ValueError("directional graph node_order differs from config")

            residual_cfg = config.get("directional_residual") or {}
            max_scale = float(residual_cfg.get("max_scale", 0.25))
            dropout = 0.5
            torch_device = torch.device(device) if device else torch.device("cpu")
            model = StaticDirectionalLaneSTGCN(
                sparse_graph,
                directional_graph,
                history_steps=n_his,
                dropout=dropout,
                max_scale=max_scale,
            ).to(torch_device)
            try:
                checkpoint = torch.load(
                    required["best.pt"],
                    map_location=torch_device,
                    weights_only=False,
                )
            except TypeError:
                checkpoint = torch.load(
                    required["best.pt"],
                    map_location=torch_device,
                )
            state = (
                checkpoint["model"]
                if isinstance(checkpoint, dict) and "model" in checkpoint
                else checkpoint
            )
            model.load_state_dict(state)
            model.eval()

            self._torch = torch
            self._model = model
            self._device = torch_device
            self._nodes = nodes
            self._features = features
            self._n_his = n_his
            self._feature_mean = feature_mean
            self._feature_std = feature_std
            self._target_mean = target_mean
            self._target_std = target_std
            self._model_name = "NarrowNet-TDP"
            self._model_version = str(
                manifest.get("model")
                or config.get("model")
                or "StaticDirectionalLaneSTGCNV1"
            )
            self._available = True
            self._reason = ""
            logger.info(
                "NarrowNet-TDP已加载: dir=%s version=%s device=%s lanes=%s",
                self.model_dir,
                self._model_version,
                torch_device,
                len(nodes),
            )
        except Exception as exc:  # noqa: BLE001
            self._available = False
            self._reason = f"load_failed:{exc}"
            logger.exception("NarrowNet-TDP模型包加载失败: %s", exc)

    def predict_vehicle_counts(
        self,
        history_frames: Sequence[dict[str, dict[str, float]]],
    ) -> tuple[dict[str, float] | None, dict[str, Any]]:
        """输入最近n_his帧车道特征输出各车道未来60秒vehicle_count

        history_frames[i][lane_id] = {feature: value}车道顺序以交付包node_order为准
        """

        meta = {
            "model": self._model_name,
            "model_version": self._model_version,
            "fallback": False,
            "fallback_reason": "",
            "inference_latency_ms": None,
        }
        if not self._available or self._model is None or self._torch is None:
            meta["fallback"] = True
            meta["fallback_reason"] = self._reason or "narrow_net_tdp_unavailable"
            return None, meta
        if len(history_frames) < self._n_his:
            meta["fallback"] = True
            meta["fallback_reason"] = "history_insufficient"
            return None, meta

        frames = list(history_frames)[-self._n_his :]
        tensor = np.zeros(
            (len(self._features), self._n_his, len(self._nodes)),
            dtype=np.float32,
        )
        for t_index, frame in enumerate(frames):
            for n_index, node in enumerate(self._nodes):
                values = frame.get(node) or {}
                for f_index, feature in enumerate(self._features):
                    tensor[f_index, t_index, n_index] = float(values.get(feature, 0.0))
        normalized = (tensor - self._feature_mean) / self._feature_std
        batch = normalized.reshape(
            1, len(self._features), self._n_his, len(self._nodes)
        )

        torch = self._torch
        started = time.perf_counter()
        try:
            with self._lock:
                with torch.inference_mode():
                    x = torch.from_numpy(np.ascontiguousarray(batch)).to(self._device)
                    output = self._model(x).reshape(1, -1).detach().cpu().numpy()[0]
            latency_ms = (time.perf_counter() - started) * 1000.0
            predicted = output * self._target_std + self._target_mean
            result = {
                node: float(max(0.0, predicted[index]))
                for index, node in enumerate(self._nodes)
            }
            meta["inference_latency_ms"] = round(latency_ms, 3)
            return result, meta
        except Exception as exc:  # noqa: BLE001
            meta["fallback"] = True
            meta["fallback_reason"] = f"inference_failed:{exc}"
            meta["inference_latency_ms"] = round(
                (time.perf_counter() - started) * 1000.0, 3
            )
            logger.exception("NarrowNet-TDP推理失败将降级moving_average: %s", exc)
            return None, meta
