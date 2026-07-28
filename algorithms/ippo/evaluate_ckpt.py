"""
IPPO checkpoint 评估 —— 固定种子测试，不更新网络。

用法：python3 evaluate_ckpt.py /path/to/model.pt
"""
import os, sys, time, json, logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [eval] %(message)s")
logger = logging.getLogger("eval")

sys.path.insert(0, "/home/kemove/devdata1/gsb/citypulse-v2x-sim")
os.environ.setdefault("SUMO_HOME", "/usr/share/sumo")
os.environ["PATH"] = "/usr/share/sumo/bin:" + os.environ.get("PATH", "")
os.environ["IPPO_MODE"] = "model"

from simulation.sumo.session import SimulationConfig, SimulationManager

INTERSECTIONS = [f"demo_{i}" for i in range(1, 21) if i != 13] + ["demo_13"]  # all 20
EVAL_SEEDS = [42, 142, 242, 342, 442]
DURATION = 300

def evaluate(model_path: str) -> dict:
    os.environ["IPPO_MODEL_PATH"] = model_path
    results = []
    
    for seed in EVAL_SEEDS:
        t0 = time.time()
        mgr = SimulationManager()
        config = SimulationConfig(
            intersection_ids=sorted(INTERSECTIONS, key=lambda x: int(x.split("_")[1])),
            period="off_peak",
            duration_seconds=DURATION,
            control_mode="algorithm",
            algorithm_transport="local",
            algorithm_module="algorithms.ippo",
            decision_interval=5.0,
            minimum_green=5.0,
            seed=seed,
            step_length=0.05,
        )
        try:
            sid = mgr.start(config)
            s = mgr.wait(sid, timeout=600)
            elapsed = time.time() - t0
            if s and s.metrics:
                results.append({
                    "seed": seed,
                    "departed": s.metrics.departed_vehicles,
                    "arrived": s.metrics.arrived_vehicles,
                    "waiting": s.metrics.total_waiting_time,
                    "elapsed": round(elapsed, 1),
                })
                logger.info("seed=%d dep=%d arr=%d wait=%.0f (%.0fs)", 
                           seed, s.metrics.departed_vehicles, s.metrics.arrived_vehicles,
                           s.metrics.total_waiting_time, elapsed)
            else:
                logger.warning("seed=%d FAILED", seed)
                results.append({"seed": seed, "error": "FAILED"})
        except Exception as e:
            logger.error("seed=%d ERR: %s", seed, str(e)[:80])
            results.append({"seed": seed, "error": str(e)[:80]})
        time.sleep(2.0)
    
    arrs = [r["arrived"] for r in results if "arrived" in r]
    waits = [r["waiting"] for r in results if "waiting" in r]
    summary = {
        "model": model_path,
        "mean_arrived": round(sum(arrs)/len(arrs), 1) if arrs else 0,
        "std_arrived": round((sum((a-sum(arrs)/len(arrs))**2 for a in arrs)/len(arrs))**0.5, 1) if arrs else 0,
        "mean_waiting": round(sum(waits)/len(waits), 1) if waits else 0,
        "best_arrived": max(arrs) if arrs else 0,
        "worst_arrived": min(arrs) if arrs else 0,
        "details": results,
    }
    return summary

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("model_path")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    
    logger.info("评估: %s", args.model_path)
    summary = evaluate(args.model_path)
    
    # 输出
    output_path = args.output or args.model_path.replace(".pt", "_eval.json")
    with open(output_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    
    logger.info("结果: mean_arr=%.1f±%.1f wait=%.0f best=%d | saved to %s",
                summary["mean_arrived"], summary["std_arrived"],
                summary["mean_waiting"], summary["best_arrived"], output_path)

if __name__ == "__main__":
    main()
