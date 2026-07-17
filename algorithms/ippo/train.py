"""
IPPO 训练脚本 —— SB3 PPO + SUMO 直连 TraCI。

训练环境的状态构造和 HTTP 推理用的是同一套 StateBuilder（controller.py），
保证训练和推理的观测向量完全一致。

用法：
  # 单路口训练
  SUMO_HOME=/usr/share/sumo python3 -m ippo.train --episodes 200

  # 指定模型保存路径
  SUMO_HOME=/usr/share/sumo python3 -m ippo.train --episodes 500 --save models/ippo_demo2

依赖：stable-baselines3, torch, numpy, SUMO
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import gymnasium as gym
import numpy as np
from gymnasium import spaces

# ── SUMO 导入（需设置 SUMO_HOME） ──
def _setup_sumo_path() -> None:
    sumo_home = os.environ.get("SUMO_HOME", "/usr/share/sumo")
    tools = os.path.join(sumo_home, "tools")
    if tools not in sys.path:
        sys.path.append(tools)

_setup_sumo_path()

import traci  # type: ignore

logger = logging.getLogger("ippo.train")


# ======================================================================
# 训练环境
# ======================================================================


class SumoTrafficEnv(gym.Env):
    """单路口信号控制 gym 环境。

    直接从 TraCI 读取车道数据，构造和 HTTP /step 相同格式的观测字典，
    再用 StateBuilder 编码为固定维度向量。
    """

    metadata = {"render_modes": []}

    def __init__(
        self,
        sumocfg: str,
        metadata_path: str,
        decision_interval: float = 5.0,
        max_steps: int = 720,        # 3600s / 5s = 720 步
        reward_scale: float = 1.0,
        seed: int = 42,
    ) -> None:
        super().__init__()

        self._sumocfg = str(sumocfg)
        self._decision_interval = decision_interval
        self._max_steps = max_steps
        self._reward_scale = reward_scale
        self._seed = seed

        # 加载元数据 — tls_manifest 格式和 HTTP /initialize 不同，需转换
        with open(metadata_path) as f:
            raw_manifest = json.load(f)
        self._metadata = _manifest_to_http_format(raw_manifest)

        # 延迟导入，避免循环
        from .controller import StateBuilder
        self._state_builder = StateBuilder(self._metadata)

        self._iid = list(self._metadata.get("intersections", {}).keys())[0]
        i_meta = self._metadata["intersections"][self._iid]
        self._phase_order = [int(p) for p in i_meta["phase_order"]]
        self._n_phases = len(self._phase_order)
        self._tl_junction_id = i_meta.get("intersection_id", "")

        # 状态空间
        state_dim = self._state_builder.state_dim
        self.observation_space = spaces.Box(
            low=0.0, high=1.0, shape=(state_dim,), dtype=np.float32
        )
        # 动作空间 = 选择相位
        self.action_space = spaces.Discrete(self._n_phases)

        self._step_count = 0
        self._total_waiting = 0.0

    # ── gym 接口 ──

    def reset(self, *, seed: Optional[int] = None, options: Optional[dict] = None) -> Tuple[np.ndarray, dict]:
        super().reset(seed=seed)

        if traci.isLoaded():
            traci.close()
            time.sleep(0.5)

        traci.start(
            ["sumo", "-c", self._sumocfg, "--seed", str(self._seed),
             "--no-warnings", "true", "--no-step-log", "true"]
        )

        # 设置信号灯为外部控制
        for tls_id in traci.trafficlight.getIDList():
            traci.trafficlight.setProgram(tls_id, "0")  # 使用 program 0（由 build_tls 生成）
            traci.trafficlight.setPhase(tls_id, 0)

        # 跑一步确保数据就绪
        traci.simulationStep()
        self._step_count = 0
        self._total_waiting = 0.0
        self._last_waiting = 0.0

        obs = self._get_obs()
        return obs, {}

    def step(self, action: int) -> Tuple[np.ndarray, float, bool, bool, dict]:
        # 1) 应用动作（action_idx → phase_id）
        if 0 <= action < len(self._phase_order):
            target_phase = self._phase_order[action]
            # 找到对应的 SUMO 相位索引
            tl_id = self._get_tl_id()
            if tl_id:
                # SUMO 的相位索引 = target_phase * 2（跳过黄灯相位）
                # 简化处理：直接设 green 相位
                sumo_phase = (target_phase - 1) * 2 if target_phase > 0 else 0
                try:
                    traci.trafficlight.setPhase(tl_id, sumo_phase)
                except traci.exceptions.TraCIException:
                    pass

        # 2) 前进 decision_interval 秒
        for _ in range(int(self._decision_interval)):
            traci.simulationStep()
        self._step_count += 1

        # 3) 构造观测 & 奖励
        obs = self._get_obs()
        # sumo-rl 验证过的 diff_waiting_time：等待时间下降 → 正奖励
        current_waiting = self._compute_total_waiting()
        reward = (self._last_waiting - current_waiting) * self._reward_scale
        self._last_waiting = current_waiting
        self._total_waiting += current_waiting

        # 4) 终止条件
        terminated = traci.simulation.getMinExpectedNumber() <= 0
        truncated = self._step_count >= self._max_steps
        done = terminated or truncated

        info = {
            "total_waiting": self._total_waiting,
            "step": self._step_count,
            "arrived": traci.simulation.getArrivedNumber(),
        }

        if done:
            traci.close()

        return obs, reward, terminated, truncated, info

    # ── 内部 ──

    def _get_tl_id(self) -> Optional[str]:
        """获取实际 SUMO tlsID。"""
        tls_ids = traci.trafficlight.getIDList()
        return tls_ids[0] if tls_ids else None

    def _get_obs(self) -> np.ndarray:
        """从 TraCI 构造和 HTTP /step 一样格式的观测，再用 StateBuilder 编码。"""
        i_meta = self._metadata["intersections"][self._iid]

        # 构造模拟的 /step observation
        lanes_obs: Dict[str, Dict[str, Any]] = {}
        # tls_manifest 的 incoming_lanes 是 {approach: [lane_ids]}，需展开
        raw_incoming = i_meta.get("incoming_lanes", {})
        if isinstance(raw_incoming, dict):
            incoming = [lid for ids in raw_incoming.values() for lid in ids]
        else:
            incoming = list(raw_incoming) if raw_incoming else []
        raw_outgoing = i_meta.get("outgoing_lanes", []) or []
        if isinstance(raw_outgoing, dict):
            outgoing = [lid for ids in raw_outgoing.values() for lid in ids]
        else:
            outgoing = list(raw_outgoing) if raw_outgoing else []
        all_lanes = incoming + outgoing

        for lane_id in all_lanes:
            try:
                lanes_obs[lane_id] = {
                    "halting_count": traci.lane.getLastStepHaltingNumber(lane_id),
                    "vehicle_count": traci.lane.getLastStepVehicleNumber(lane_id),
                    "waiting_time": traci.lane.getWaitingTime(lane_id),
                    "mean_speed": traci.lane.getLastStepMeanSpeed(lane_id),
                    "occupancy": traci.lane.getLastStepOccupancy(lane_id),
                }
            except traci.exceptions.TraCIException:
                lanes_obs[lane_id] = {
                    "halting_count": 0, "vehicle_count": 0,
                    "waiting_time": 0.0, "mean_speed": 0.0, "occupancy": 0.0,
                }

        # 获取当前相位
        tl_id = self._get_tl_id()
        current_phase = 0
        if tl_id:
            sumo_phase = traci.trafficlight.getPhase(tl_id)
            # 从 SUMO 相位索引反推 phase_id
            current_phase = sumo_phase // 2 + 1
            if current_phase > self._n_phases:
                current_phase = 1

        stage_elapsed = 0.0
        if tl_id:
            stage_elapsed = traci.trafficlight.getPhaseDuration(tl_id)

        fake_obs = {
            "intersections": {
                self._iid: {
                    "current_phase": current_phase,
                    "stage": "GREEN",
                    "stage_elapsed": stage_elapsed,
                    "lanes": lanes_obs,
                }
            }
        }

        return self._state_builder.build(self._iid, fake_obs)

    def _compute_total_waiting(self) -> float:
        """所有 lane 的累计等待时间（秒）。"""
        i_meta = self._metadata["intersections"][self._iid]
        total = 0.0
        for lane_id in i_meta.get("incoming_lanes", []):
            try:
                total += traci.lane.getWaitingTime(lane_id)
            except traci.exceptions.TraCIException:
                pass
        return total


# ======================================================================
# 格式转换：tls_manifest.json → HTTP /initialize 格式
# ======================================================================


def _manifest_to_http_format(raw: dict) -> dict:
    """将 SIM 组内部的 tls_manifest.json 转为 StateBuilder 可理解的 HTTP 格式。"""
    http = {"intersections": {}}
    for iid, im in raw.get("intersections", {}).items():
        # incoming_lanes: {approach: [lane_ids]} → flat list
        raw_in = im.get("incoming_lanes", {})
        if isinstance(raw_in, dict):
            incoming = [lid for ids in raw_in.values() for lid in ids]
        else:
            incoming = list(raw_in) if raw_in else []

        # outgoing_lanes: 从 connections 中收集 to_lane（或 to_edge_lane）
        outgoing = []
        seen = set()
        for conn in im.get("connections", []):
            from_edge = conn.get("from_edge", "")
            to_edge = conn.get("to_edge", "")
            from_idx = conn.get("from_lane", 0)
            to_idx = conn.get("to_lane", 0)
            to_lid = f"{to_edge}_{to_idx}"
            if to_lid not in seen:
                seen.add(to_lid)
                outgoing.append(to_lid)
            # 也收集 from_lane 作为 outgoing（对向车道）
            from_lid = f"{from_edge}_{from_idx}"
            if from_lid not in seen:
                seen.add(from_lid)

        http["intersections"][iid] = {
            "intersection_id": iid,
            "phase_order": im.get("phase_order", []),
            "incoming_lanes": incoming,
            "outgoing_lanes": outgoing,
        }
    return http


# ======================================================================
# 训练入口
# ======================================================================


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(description="IPPO 训练脚本")
    parser.add_argument("--episodes", type=int, default=200, help="训练 episode 数（默认 200）")
    parser.add_argument("--timesteps", type=int, default=0, help="或指定总 timesteps（覆盖 --episodes）")
    parser.add_argument("--save", type=str, default="models/ippo_demo2", help="模型保存路径")
    parser.add_argument("--sumocfg", type=str, default="", help="sumocfg 路径（默认自动找 demo_2 morning_peak）")
    parser.add_argument("--metadata", type=str, default="", help="元数据 JSON 路径（默认 tls_manifest）")
    parser.add_argument("--lr", type=float, default=3e-4, help="学习率")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    args = parser.parse_args()

    # ── 找 sumocfg ──
    repo_root = Path(__file__).resolve().parents[2]  # algorithms/
    generated_dir = repo_root / "data" / "maps" / "sumo" / "generated"

    sumocfg = args.sumocfg
    if not sumocfg:
        sumocfg = str(generated_dir / "official_traffic_demo_2_morning_peak.sumocfg")
    metadata_path = args.metadata
    if not metadata_path:
        metadata_path = str(generated_dir / "tls_manifest.json")

    if not os.path.exists(sumocfg):
        logger.error("sumocfg 不存在: %s", sumocfg)
        logger.info("请先运行 build_tls: SUMO_HOME=/usr/share/sumo python3 -m simulation.sumo.build_tls --intersections demo_2")
        sys.exit(1)
    if not os.path.exists(metadata_path):
        logger.error("元数据不存在: %s", metadata_path)
        sys.exit(1)

    logger.info("sumocfg: %s", sumocfg)
    logger.info("metadata: %s", metadata_path)

    # ── 加载元数据，计算 state_dim ──
    with open(metadata_path) as f:
        metadata = json.load(f)
    from .controller import StateBuilder
    sb = StateBuilder(metadata)
    state_dim = sb.state_dim
    iid = list(metadata.get("intersections", {}).keys())[0]
    n_actions = len(metadata["intersections"][iid]["phase_order"])

    logger.info("state_dim=%d n_actions=%d", state_dim, n_actions)

    # ── 创建环境 ──
    env = SumoTrafficEnv(
        sumocfg=sumocfg,
        metadata_path=metadata_path,
        decision_interval=5.0,
        max_steps=720,
    )

    logger.info("观测空间: %s", env.observation_space)
    logger.info("动作空间: %s", env.action_space)

    # ── 训练 ──
    try:
        from stable_baselines3 import PPO
        from stable_baselines3.common.callbacks import EvalCallback, StopTrainingOnNoModelImprovement
    except ImportError:
        logger.error("stable-baselines3 未安装，请运行: pip install stable-baselines3")
        sys.exit(1)

    model = PPO(
        "MlpPolicy",
        env,
        learning_rate=args.lr,
        n_steps=1024,
        batch_size=64,
        n_epochs=10,
        gamma=0.99,
        gae_lambda=0.95,
        clip_range=0.2,
        ent_coef=0.01,
        verbose=1,
        seed=args.seed,
    )

    total_timesteps = args.timesteps if args.timesteps > 0 else args.episodes * env._max_steps

    logger.info("开始训练，目标: %d timesteps (~%d episodes)", total_timesteps, args.episodes)

    model.learn(total_timesteps=total_timesteps)

    # ── 保存 ──
    save_dir = repo_root / "algorithms" / args.save
    save_dir = Path(args.save) if os.path.isabs(args.save) else save_dir
    save_dir.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(save_dir))
    logger.info("模型已保存: %s.zip", save_dir)

    env.close()


if __name__ == "__main__":
    main()
