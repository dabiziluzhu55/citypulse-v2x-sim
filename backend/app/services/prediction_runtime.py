# STGCN在线短时预测运行时：模型包一次性加载，不可用时由调用方降级moving_average

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_FEATURES = (
    "vehicle_count",
    "halting_count",
    "mean_speed",
    "occupancy",
)


@dataclass(frozen=True)
class PredictionRuntimeStatus:
    available: bool
    model: str
    model_version: str
    reason: str = ""


def _calc_chebynet_gso(adjacency: np.ndarray) -> np.ndarray:
    """对称归一化拉普拉斯并缩放到Chebyshev约定区间。"""

    adj = np.asarray(adjacency, dtype=np.float32)
    if adj.ndim != 2 or adj.shape[0] != adj.shape[1]:
        raise ValueError(f"adjacency must be square, got {adj.shape}")
    deg = adj.sum(axis=1)
    inv_sqrt = np.zeros_like(deg)
    np.divide(1.0, np.sqrt(deg), out=inv_sqrt, where=deg > 0)
    d = np.diag(inv_sqrt)
    norm_adj = d @ adj @ d
    laplacian = np.eye(adj.shape[0], dtype=np.float32) - norm_adj
    eigenvalues = np.linalg.eigvalsh(laplacian)
    lambda_max = float(eigenvalues.max()) if eigenvalues.size else 2.0
    if lambda_max < 1e-6:
        lambda_max = 2.0
    return ((2.0 / lambda_max) * laplacian - np.eye(adj.shape[0], dtype=np.float32)).astype(
        np.float32
    )


def _import_stgcn_models(stgcn_root: Path | None):
    candidates: list[Path] = []
    if stgcn_root is not None:
        candidates.append(stgcn_root)
    env_root = os.environ.get("STGCN_ROOT", "").strip()
    if env_root:
        candidates.append(Path(env_root))
    for root in candidates:
        resolved = root.expanduser().resolve()
        if not resolved.is_dir():
            continue
        text = str(resolved)
        if text not in sys.path:
            sys.path.insert(0, text)
        try:
            from model import models  # type: ignore[import-not-found]

            return models
        except Exception as exc:  # noqa: BLE001
            logger.warning("无法从STGCN_ROOT导入模型实现: %s (%s)", resolved, exc)
    try:
        from model import models  # type: ignore[import-not-found]

        return models
    except Exception:
        return None


class PredictionRuntime:
    """进程内单例式STGCN推理；加载失败时available=False，不抛到快照路径。"""

    def __init__(
        self,
        model_dir: Path | None,
        *,
        stgcn_root: Path | None = None,
        device: str | None = None,
    ) -> None:
        self.model_dir = Path(model_dir).expanduser() if model_dir else None
        self.stgcn_root = Path(stgcn_root).expanduser() if stgcn_root else None
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
        self._model_name = "STGCN"
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
        stgcn_root: str | Path | None = None,
        device: str | None = None,
    ) -> "PredictionRuntime":
        path = Path(model_dir).expanduser() if model_dir else None
        root = Path(stgcn_root).expanduser() if stgcn_root else None
        return cls(path, stgcn_root=root, device=device)

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
        required = {
            "stgcn_best.pt": self.model_dir / "stgcn_best.pt",
            "adjacency.npz": self.model_dir / "adjacency.npz",
            "normalization_and_nodes.json": self.model_dir / "normalization_and_nodes.json",
            "model_manifest.json": self.model_dir / "model_manifest.json",
        }
        missing = [name for name, path in required.items() if not path.is_file()]
        if missing:
            self._available = False
            self._reason = f"missing_files:{','.join(missing)}"
            logger.warning("STGCN模型包不完整(%s): %s", self.model_dir, self._reason)
            return
        try:
            import torch
        except Exception as exc:  # noqa: BLE001
            self._available = False
            self._reason = f"torch_unavailable:{exc}"
            logger.warning("STGCN需要torch，当前不可用: %s", exc)
            return

        models = _import_stgcn_models(self.stgcn_root)
        if models is None:
            self._available = False
            self._reason = "stgcn_impl_unavailable"
            logger.warning(
                "未找到STGCN实现，请设置STGCN_ROOT；已跳过在线STGCN，保留moving_average降级"
            )
            return

        try:
            norm_payload = json.loads(
                required["normalization_and_nodes.json"].read_text(encoding="utf-8")
            )
            manifest = json.loads(required["model_manifest.json"].read_text(encoding="utf-8"))
            nodes = tuple(
                str(item)
                for item in (
                    norm_payload.get("nodes")
                    or norm_payload.get("lanes")
                    or manifest.get("nodes")
                    or ()
                )
            )
            if len(nodes) != 20:
                raise ValueError(f"expected 20 nodes, got {len(nodes)}")
            features = tuple(
                str(item)
                for item in (
                    norm_payload.get("features")
                    or manifest.get("features")
                    or DEFAULT_FEATURES
                )
            )
            if len(features) != 4:
                raise ValueError(f"expected 4 features, got {features}")
            normalization = norm_payload.get("normalization") or norm_payload
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
                norm_payload.get("n_his")
                or manifest.get("n_his")
                or 12
            )
            adjacency = np.load(required["adjacency.npz"])["adjacency"].astype(np.float32)
            if adjacency.shape != (20, 20):
                raise ValueError(f"adjacency shape must be (20,20), got {adjacency.shape}")
            gso = _calc_chebynet_gso(adjacency)

            torch_device = torch.device(
                device
                if device
                else ("cuda" if torch.cuda.is_available() else "cpu")
            )
            model_args = SimpleNamespace(
                n_his=n_his,
                Kt=3,
                Ks=3,
                stblock_num=2,
                act_func="glu",
                graph_conv_type="cheb_graph_conv",
                gso=torch.from_numpy(gso).to(torch_device),
                enable_bias=True,
                droprate=float(manifest.get("dropout", 0.0) or 0.0),
            )
            blocks = [[len(features)], [64, 16, 64], [64, 16, 64], [128, 128], [1]]
            model = models.STGCNChebGraphConv(model_args, blocks, len(nodes)).to(torch_device)
            try:
                checkpoint = torch.load(
                    required["stgcn_best.pt"],
                    map_location=torch_device,
                    weights_only=False,
                )
            except TypeError:
                checkpoint = torch.load(
                    required["stgcn_best.pt"],
                    map_location=torch_device,
                )
            state = checkpoint["model"] if isinstance(checkpoint, dict) and "model" in checkpoint else checkpoint
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
            self._model_name = str(
                manifest.get("model")
                or manifest.get("model_name")
                or "official20-stgcn-v1"
            )
            self._model_version = str(
                manifest.get("model_version")
                or manifest.get("version")
                or "v1"
            )
            self._available = True
            self._reason = ""
            logger.info(
                "STGCN已加载: dir=%s model=%s version=%s device=%s",
                self.model_dir,
                self._model_name,
                self._model_version,
                torch_device,
            )
        except Exception as exc:  # noqa: BLE001
            self._available = False
            self._reason = f"load_failed:{exc}"
            logger.exception("STGCN模型包加载失败: %s", exc)

    def predict_vehicle_counts(
        self,
        history_frames: Sequence[dict[str, dict[str, float]]],
    ) -> tuple[dict[str, float] | None, dict[str, Any]]:
        """输入最近n_his帧节点特征，输出各节点未来60秒vehicle_count。

        history_frames[i][node] = {feature: value}，节点顺序以模型包为准。
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
            meta["fallback_reason"] = self._reason or "stgcn_unavailable"
            return None, meta
        if len(history_frames) < self._n_his:
            meta["fallback"] = True
            meta["fallback_reason"] = "history_insufficient"
            return None, meta

        frames = list(history_frames)[-self._n_his :]
        # [4, 12, 20]
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
        batch = normalized.reshape(1, len(self._features), self._n_his, len(self._nodes))

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
            logger.exception("STGCN推理失败，将降级moving_average: %s", exc)
            return None, meta
