"""Compatibility entry point for the new official signal runner."""

import sys
import warnings
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from simulation.sumo.run import main


if __name__ == "__main__":
    warnings.warn(
        "algorithms.baseline.run_sumo_control is deprecated; "
        "use python -m simulation.sumo.run instead.",
        DeprecationWarning,
        stacklevel=1,
    )
    main()
