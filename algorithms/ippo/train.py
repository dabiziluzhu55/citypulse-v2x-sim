"""
IPPO 训练脚本 —— 多路口参数共享 PPO。

用法:
  IPPO_MODE=train python3 -m ippo.train --episodes 200

依赖：服务器上的 SimulationManager + Protocol 2.0 local transport。
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

if tools not in sys.path:
        sys.path.append(tools)

_setup_sumo_path()

import traci  # type: ignore

logger = logging.getLogger("ippo.train")

CHECKPOINT_SAVE_PATH = "/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/models/ippo_20"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes", type=int, default=200)
    parser.add_argument("--duration", type=int, default=300)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--save", type=str, default=CHECKPOINT_SAVE_PATH)
    args = parser.parse_args()

    logger.info("IPPO 开始训练: %d episodes × %ds, seed=%d", args.episodes, args.duration, args.seed)

    for ep in range(1, args.episodes + 1):
        mgr = SimulationManager()
        config = SimulationConfig(
            intersection_ids=INTERSECTIONS,
            period="off_peak",
            duration_seconds=args.duration,
            control_mode="algorithm",
            algorithm_transport="local",
            algorithm_module="algorithms.ippo",
            decision_interval=5.0,
            minimum_green=5.0,
            seed=args.seed + ep,
            step_length=0.05,
        )

        t0 = time.time()
        try:
            sid = mgr.start(config)
            s = mgr.wait(sid, timeout=max(600, args.duration * 3))
            elapsed = time.time() - t0

            if s is None:
                logger.warning("EP%d TIMEOUT", ep)
            elif s.metrics and s.state != "FAILED":
                logger.info(
                    "EP%d OK (%.0fs) departed=%d arrived=%d",
                    ep, elapsed, s.metrics.departed_vehicles, s.metrics.arrived_vehicles,
                )
            else:
                logger.info(
                    "EP%d FAILED (%.0fs) departed=%d arrived=%d",
                    ep, elapsed,
                    s.metrics.departed_vehicles if s and s.metrics else 0,
                    s.metrics.arrived_vehicles if s and s.metrics else 0,
                )
        except Exception as e:
            logger.error("EP%d ERR: %s", ep, str(e)[:100])

        # 内存清理：防 SUMO malloc 崩溃
        import gc
        del mgr
        gc.collect()
        try:
            import torch
            torch.cuda.empty_cache()
        except:
            pass
        time.sleep(2.0)

    # 保存最终模型
    from algorithms.ippo.controller import _model
    if _model is not None:
        save_path = args.save + ".pt"
        import torch
        torch.save(_model.state_dict(), save_path)
        logger.info("模型已保存: %s", save_path)

    logger.info("训练完成: %d episodes", args.episodes)


if __name__ == "__main__":
    main()