"""Make bitsandbytes find PyTorch-bundled CUDA 13 runtime libs."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path


_PRELOAD_NAMES = (
    "libnvJitLink.so.13",
    "libnvJitLink.so.12",
    "libcudart.so.13",
    "libcudart.so.12",
)


def ensure_cuda_runtime_libs() -> str | None:
    try:
        import nvidia
    except Exception:
        nvidia = None
    candidates: list[Path] = []
    extra = os.environ.get("TRAFFIC_LLM_CUDA_LIB")
    if extra:
        candidates.append(Path(extra))
    if nvidia is not None:
        root = Path(nvidia.__file__).resolve().parent
        candidates.append(root / "cu13" / "lib")
        candidates.append(root / "cuda_runtime" / "lib")
    conda = os.environ.get("CONDA_PREFIX")
    if conda:
        candidates.append(Path(conda) / "lib/python3.10/site-packages/nvidia/cu13/lib")
    added: list[str] = []
    current = os.environ.get("LD_LIBRARY_PATH", "")
    parts = [item for item in current.split(os.pathsep) if item]
    for path in candidates:
        if path.is_dir() and str(path) not in parts:
            parts.insert(0, str(path))
            added.append(str(path))
    if parts:
        os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(parts)
    for directory in parts:
        root = Path(directory)
        for name in _PRELOAD_NAMES:
            so_path = root / name
            if so_path.is_file():
                try:
                    ctypes.CDLL(str(so_path), mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    continue
    return added[0] if added else (parts[0] if parts else None)
