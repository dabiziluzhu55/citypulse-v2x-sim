"""
IPPO (Independent PPO) 策略控制器 —— 状态构造 + 模型推理。

训练和推理共享同一个状态构造逻辑：
  状态向量 = [当前相位 one-hot] + [阶段耗时/最大绿] + Σ [车道特征 × 固定顺序]

车道特征（每车道 5 维）：halting_count, vehicle_count, waiting_time/100,
                        mean_speed/max_speed, occupancy/100
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)


class StateBuilder:
    """将 /initialize 元数据和 /step 观测编码为固定维度的向量。

    同一套 StateBuilder 在训练（TraCI）和推理（HTTP）中复用，保证向量一致。
    """

    def __init__(
        self,
        metadata: Dict[str, Any],
        *,
        max_waiting: float = 200.0,
        max_speed_global: float = 20.0,
        max_occupancy: float = 100.0,
        max_stage_elapsed: float = 120.0,
    ) -> None:
        self._max_waiting = max_waiting
        self._max_speed_global = max_speed_global
        self._max_occupancy = max_occupancy
        self._max_stage_elapsed = max_stage_elapsed

        # ── 按路口构建索引 ──
        self._indices: Dict[str, _IntersectionStateIndex] = {}
        for iid, i_meta in metadata.get("intersections", {}).items():
            self._indices[iid] = _build_state_index(i_meta)

    @property
    def state_dim(self) -> int:
        """所有路口的最大状态维度（多路口取 max）。"""
        return max(
            (ix.state_dim for ix in self._indices.values()),
            default=0,
        )

    def build(self, intersection_id: str, observation: Dict[str, Any]) -> np.ndarray:
        """构造单个路口的状态向量。"""
        ix = self._indices[intersection_id]
        i_obs = observation["intersections"].get(intersection_id)
        if i_obs is None:
            return np.zeros(ix.state_dim, dtype=np.float32)

        lanes_obs: Dict[str, Any] = i_obs.get("lanes", {})
        current_phase: int = i_obs.get("current_phase", ix.phase_order[0] if ix.phase_order else 0)
        stage_elapsed: float = float(i_obs.get("stage_elapsed", 0.0))

        vec_parts: List[np.ndarray] = []

        # 1) 当前相位 one-hot
        phase_idx = ix.phase_index.get(current_phase, 0)
        phase_oh = np.zeros(ix.n_phases, dtype=np.float32)
        if 0 <= phase_idx < ix.n_phases:
            phase_oh[phase_idx] = 1.0
        vec_parts.append(phase_oh)

        # 2) 阶段耗时归一化
        stage_norm = np.array(
            [min(stage_elapsed / max(self._max_stage_elapsed, 1.0), 1.0)],
            dtype=np.float32,
        )
        vec_parts.append(stage_norm)

        # 3) 按固定顺序拼接车道特征
        for lane_id in ix.lane_order:
            obs = lanes_obs.get(lane_id, {})
            feat = np.array(
                [
                    float(obs.get("halting_count", 0)),
                    float(obs.get("vehicle_count", 0)),
                    min(float(obs.get("waiting_time", 0.0)) / max(self._max_waiting, 1.0), 1.0),
                    min(float(obs.get("mean_speed", 0.0)) / max(self._max_speed_global, 1.0), 1.0),
                    min(float(obs.get("occupancy", 0.0)) / max(self._max_occupancy, 1.0), 1.0),
                ],
                dtype=np.float32,
            )
            vec_parts.append(feat)

        return np.concatenate(vec_parts)


class IPPOController:
    """IPPO 推理控制器 —— 加载训练好的模型，每步做前向推理。

    支持三种模式：
      - "model"  : 加载 PyTorch 模型文件
      - "random" : 随机采样（模型未训练时的占位）
      - "fixed"  : 始终输出第一个相位（调试用）
    """

    def __init__(
        self,
        metadata: Dict[str, Any],
        *,
        mode: str = "random",
        model_path: Optional[str] = None,
    ) -> None:
        self._state_builder = StateBuilder(metadata)
        self._mode = mode
        self._model = None

        # ── 预建每路口 phase_order 映射 ──
        self._phase_orders: Dict[str, List[int]] = {}
        for iid, i_meta in metadata.get("intersections", {}).items():
            self._phase_orders[iid] = [int(p) for p in i_meta.get("phase_order", [])]

        # 加载模型
        if mode == "model" and model_path:
            self._load_model(model_path, metadata)
        elif mode == "model":
            logger.warning("mode='model' 但未指定 model_path，回退到 random")
            self._mode = "random"

        logger.info(
            "IPPOController 初始化: mode=%s 路口数=%d state_dim=%d",
            self._mode,
            len(self._phase_orders),
            self._state_builder.state_dim,
        )

    def _load_model(self, model_path: str, metadata: Dict[str, Any]) -> None:
        """加载 SB3 PPO 模型。"""
        try:
            from stable_baselines3 import PPO
        except ImportError:
            logger.warning("stable-baselines3 未安装，无法加载模型，回退到 random")
            self._mode = "random"
            return

        if not os.path.exists(model_path):
            logger.warning("模型文件 %s 不存在，回退到 random", model_path)
            self._mode = "random"
            return

        self._model = PPO.load(model_path)
        logger.info("已加载模型: %s", model_path)

    def compute_actions(self, observation: Dict[str, Any]) -> Dict[str, Optional[int]]:
        """返回 {路口id: 相位id}。"""
        actions: Dict[str, Optional[int]] = {}

        for iid in self._phase_orders:
            if iid not in observation.get("intersections", {}):
                continue
            actions[iid] = self._select_action(iid, observation)

        return actions

    def _select_action(self, iid: str, observation: Dict[str, Any]) -> Optional[int]:
        """为单个路口选动作。"""
        phase_order = self._phase_orders.get(iid, [])
        if not phase_order:
            return None

        state = self._state_builder.build(iid, observation)

        if self._mode == "random":
            return int(np.random.choice(phase_order))
        elif self._mode == "fixed":
            return phase_order[0]
        elif self._mode == "model" and self._model is not None:
            action_idx, _ = self._model.predict(state, deterministic=True)
            idx = int(action_idx)
            if 0 <= idx < len(phase_order):
                return phase_order[idx]
            return phase_order[0]
        else:
            return phase_order[0]


# ======================================================================
# 内部辅助
# ======================================================================


class _IntersectionStateIndex:
    """单个路口的状态编码索引。"""

    __slots__ = ("phase_order", "phase_index", "n_phases", "lane_order", "state_dim")

    def __init__(self) -> None:
        self.phase_order: List[int] = []
        self.phase_index: Dict[int, int] = {}
        self.n_phases: int = 0
        self.lane_order: List[str] = []
        self.state_dim: int = 0


def _build_state_index(i_meta: Dict[str, Any]) -> _IntersectionStateIndex:
    """预计算状态向量结构。"""
    ix = _IntersectionStateIndex()

    # 相位顺序
    ix.phase_order = [int(p) for p in i_meta.get("phase_order", [])]
    ix.phase_index = {p: i for i, p in enumerate(ix.phase_order)}
    ix.n_phases = len(ix.phase_order)

    # 车道固定顺序：incoming → outgoing
    incoming = list(i_meta.get("incoming_lanes", []))
    outgoing = list(i_meta.get("outgoing_lanes", []))
    ix.lane_order = incoming + outgoing

    # 维度 = phase_one_hot(n_phases) + stage_norm(1) + lane_feat(5) × n_lanes
    ix.state_dim = ix.n_phases + 1 + 5 * len(ix.lane_order)

    return ix
