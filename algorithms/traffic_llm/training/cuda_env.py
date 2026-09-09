"""Make bitsandbytes find PyTorch-bundled CUDA 13 runtime libs."""

from __future__ import annotations

import os
from pathlib import Path


def ensure_cuda_runtime_libs() -> str | None:
    try:
        import nvidia
    except Exception:
        nvidia = None
    candidates: list[Path] = []
    if nvidia is not None:
        root = Path(nvidia.__file__).resolve().parent
        candidates.append(root / "cu13" / "lib")
        candidates.append(root / "cuda_runtime" / "lib")
    extra = os.environ.get("TRAFFIC_LLM_CUDA_LIB")
    if extra:
        candidates.insert(0, Path(extra))
    added: list[str] = []
    current = os.environ.get("LD_LIBRARY_PATH", "")
    parts = [item for item in current.split(os.pathsep) if item]
    for path in candidates:
        if path.is_dir() and str(path) not in parts:
            parts.insert(0, str(path))
            added.append(str(path))
    if added:
        os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(parts)
    return added[0] if added else (parts[0] if parts else None)
