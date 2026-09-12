import os
from pathlib import Path
import subprocess
import sys


def test_real_lifespan_in_clean_interpreter():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(root / 'scripts/deployment/check_backend_isolation.py'), '--imports-only'],
        cwd=root, env={**os.environ, 'CITYPULSE_ENV_FILE': ''},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
