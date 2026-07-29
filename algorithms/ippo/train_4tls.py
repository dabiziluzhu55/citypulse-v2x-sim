"""IPPO 4 路口快速训练验证。"""
import os, sys, time, logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("ippo.4tls")

sys.path.insert(0, "/home/kemove/devdata1/gsb/citypulse-v2x-sim")
os.environ.setdefault("SUMO_HOME", "/usr/share/sumo")
os.environ["PATH"] = "/usr/share/sumo/bin:" + os.environ.get("PATH", "")
os.environ["IPPO_MODE"] = "train"

from simulation.sumo.session import SimulationConfig, SimulationManager

INTERSECTIONS = ["demo_1", "demo_2", "demo_3", "demo_4"]
EPISODES = 16
DURATION = 300

logger.info("IPPO v3 4路口验证: %d episodes × %ds", EPISODES, DURATION)

for ep in range(1, EPISODES + 1):
    mgr = SimulationManager()
    config = SimulationConfig(
        intersection_ids=INTERSECTIONS,
        period="off_peak",
        duration_seconds=DURATION,
        control_mode="algorithm",
        algorithm_transport="local",
        algorithm_module="algorithms.ippo",
        decision_interval=5.0,
        minimum_green=5.0,
        seed=42 + ep,
        step_length=0.05,
    )
    t0 = time.time()
    try:
        sid = mgr.start(config)
        s = mgr.wait(sid, timeout=max(300, DURATION * 2))
        elapsed = time.time() - t0
        if s is None:
            logger.warning("EP%d TIMEOUT", ep)
        elif s.metrics:
            logger.info("EP%d OK (%.0fs) dep=%d arr=%d", ep, elapsed,
                        s.metrics.departed_vehicles, s.metrics.arrived_vehicles)
        else:
            logger.info("EP%d FAILED (%.0fs)", ep, elapsed)
    except Exception as e:
        logger.error("EP%d ERR: %s", ep, str(e)[:100])
    time.sleep(2.0)

# 保存模型
from algorithms.ippo.controller import _model
if _model is not None:
    import torch
    save_path = "/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/models/ippo_v3_4tls.pt"
    torch.save(_model.state_dict(), save_path)
    logger.info("模型已保存: %s", save_path)

logger.info("4路口验证完成")
