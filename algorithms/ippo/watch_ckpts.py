"""
Checkpoint watcher —— 监控新 checkpoint 并自动评估。

用法：python3 watch_ckpts.py &
  后台运行，发现新 .pt 文件且未评估过 → 自动跑 evaluate_ckpt.py
"""
import os, sys, time, json, subprocess
from pathlib import Path

CKPT_DIR = Path("/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/ippo/checkpoints")
EVAL_SCRIPT = Path("/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/ippo/evaluate_ckpt.py")
MANIFEST = CKPT_DIR / "evaluated.json"
SLEEP_SEC = 120  # 每 2 分钟扫描一次

def load_manifest():
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())
    return {}

def save_manifest(m):
    MANIFEST.write_text(json.dumps(m, indent=2))

def is_stable(path: Path, wait_sec=30):
    """等文件大小稳定后再评估"""
    s1 = path.stat().st_size
    time.sleep(wait_sec)
    s2 = path.stat().st_size
    return s1 == s2 and s1 > 0

print(f"[watcher] 监控 {CKPT_DIR}，每 {SLEEP_SEC}s 扫描")
evaluated = load_manifest()

while True:
    for pt in sorted(CKPT_DIR.glob("ippo_v3_ep*.pt")):
        name = pt.name
        if name in evaluated:
            continue
        if not is_stable(pt):
            continue
        
        output = str(pt).replace(".pt", "_eval.json")
        print(f"[watcher] 评估: {name}")
        try:
            subprocess.run([
                "python3.10", str(EVAL_SCRIPT), str(pt), "--output", output
            ], check=True, timeout=3600, cwd=str(CKPT_DIR.parent.parent))
            evaluated[name] = {"evaluated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
            save_manifest(evaluated)
            print(f"[watcher] 完成: {name}")
        except Exception as e:
            print(f"[watcher] 失败: {name} - {e}")
    
    time.sleep(SLEEP_SEC)
