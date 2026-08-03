"""Convenience entry point for four-intersection parallel IPPO training."""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from algorithms.ippo.controller import MODEL_VERSION  # noqa: E402
from algorithms.ippo.parallel_train import main as parallel_main  # noqa: E402


DEFAULT_SAVE = (
    REPO_ROOT / "algorithms" / "models" / f"ippo_{MODEL_VERSION}_parallel_4tls.pt"
)


def main(argv: list[str] | None = None) -> int:
    overrides = list(sys.argv[1:] if argv is None else argv)
    defaults = [
        "--intersections",
        "4",
        "--workers",
        "4",
        "--episodes",
        "16",
        "--duration",
        "300",
        "--save",
        str(DEFAULT_SAVE),
    ]
    return parallel_main([*defaults, *overrides])


if __name__ == "__main__":
    raise SystemExit(main())
