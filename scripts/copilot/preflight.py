#!/usr/bin/env python3
"""Validate the local Qwen and RAG artifacts before starting Copilot.

The artifact checks are dependency-free.  Optional runtime checks verify the
Python packages, CUDA runtime and actual Chroma collections without loading an
Embedding model, so the command can fail fast before a Qwen model is loaded.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
from typing import Any, Mapping


DEFAULT_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
DEFAULT_EMBEDDING_DIMENSION = 1024


@dataclass(frozen=True)
class Check:
    label: str
    ok: bool
    detail: str


def _resolve(value: str, root: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (root / path).resolve()


def _first_text(payload: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _load_json(path: Path, label: str, checks: list[Check]) -> Mapping[str, Any] | None:
    if not path.is_file():
        checks.append(Check(label, False, f"missing file: {path}"))
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        checks.append(Check(label, False, f"invalid JSON: {path} ({exc})"))
        return None
    if not isinstance(payload, Mapping):
        checks.append(Check(label, False, f"JSON root must be an object: {path}"))
        return None
    checks.append(Check(label, True, str(path)))
    return payload


def _check_model_dir(path_text: str, label: str, checks: list[Check]) -> None:
    if not path_text.strip():
        checks.append(Check(label, False, "path is not configured"))
        return
    path = Path(path_text).expanduser()
    if not path.is_dir():
        checks.append(Check(label, False, f"directory does not exist: {path}"))
        return
    if not (path / "config.json").is_file():
        checks.append(Check(label, False, f"config.json is missing: {path}"))
        return
    checks.append(Check(label, True, str(path)))


def _check_chunks(path_text: str, label: str, expected_count: int | None, checks: list[Check]) -> None:
    if not path_text.strip():
        if expected_count is None:
            return
        checks.append(Check(label, False, "path is not configured"))
        return
    path = Path(path_text).expanduser()
    if not path.is_file():
        checks.append(Check(label, False, f"missing file: {path}"))
        return
    try:
        count = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError as exc:
        checks.append(Check(label, False, f"cannot read {path}: {exc}"))
        return
    if count <= 0:
        checks.append(Check(label, False, f"no chunks found: {path}"))
        return
    if expected_count is not None and count != expected_count:
        checks.append(
            Check(label, False, f"chunk count {count} does not match index manifest {expected_count}: {path}")
        )
        return
    checks.append(Check(label, True, f"{count} chunks: {path}"))


def _check_index(
    *,
    label: str,
    index_dir_text: str,
    source_manifest_text: str,
    collection: str,
    embedding_model: str,
    embedding_dimension: int,
    chunks_text: str,
    chunks_required: bool,
    check_chroma: bool,
    root: Path,
    checks: list[Check],
) -> None:
    if not index_dir_text.strip():
        checks.append(Check(label, False, "index directory is not configured"))
        return
    if not source_manifest_text.strip():
        checks.append(Check(label, False, "source manifest is not configured"))
        return

    index_dir = _resolve(index_dir_text, root)
    source_manifest_path = _resolve(source_manifest_text, root)
    if not index_dir.is_dir():
        checks.append(Check(f"{label} directory", False, f"missing directory: {index_dir}"))
        return
    index_metadata = _load_json(index_dir / "index_manifest.json", f"{label} index manifest", checks)
    source_manifest = _load_json(source_manifest_path, f"{label} source manifest", checks)
    if index_metadata is None or source_manifest is None:
        return

    source_version = _first_text(source_manifest, ("version", "knowledge_version", "manifest_version"))
    source_revision = _first_text(
        source_manifest,
        ("project_revision", "code_revision", "source_revision", "revision"),
    )
    index_version = _first_text(index_metadata, ("knowledge_version", "version"))
    index_revision = _first_text(index_metadata, ("code_revision", "source_revision", "revision"))
    if not source_version or not source_revision:
        checks.append(Check(f"{label} freshness metadata", False, "source manifest lacks version/revision"))
    elif index_version != source_version or index_revision != source_revision:
        checks.append(
            Check(
                f"{label} freshness metadata",
                False,
                f"index={index_version}/{index_revision}, source={source_version}/{source_revision}",
            )
        )
    else:
        checks.append(Check(f"{label} freshness metadata", True, f"{source_version}/{source_revision}"))

    actual_model = str(index_metadata.get("embedding_model", "")).strip()
    if actual_model != embedding_model:
        checks.append(
            Check(f"{label} embedding model", False, f"index={actual_model!r}, expected={embedding_model!r}")
        )
    else:
        checks.append(Check(f"{label} embedding model", True, actual_model))

    try:
        actual_dimension = int(index_metadata.get("embedding_dimension", 0))
    except (TypeError, ValueError):
        actual_dimension = 0
    if actual_dimension != embedding_dimension:
        checks.append(
            Check(
                f"{label} embedding dimension",
                False,
                f"index={actual_dimension}, expected={embedding_dimension}",
            )
        )
    else:
        checks.append(Check(f"{label} embedding dimension", True, str(actual_dimension)))

    actual_collection = str(index_metadata.get("collection_name", "")).strip()
    if actual_collection != collection:
        checks.append(
            Check(f"{label} collection", False, f"index={actual_collection!r}, expected={collection!r}")
        )
    else:
        checks.append(Check(f"{label} collection", True, actual_collection))

    try:
        chunk_count = int(index_metadata.get("chunk_count", 0))
    except (TypeError, ValueError):
        chunk_count = 0
    if chunk_count <= 0:
        checks.append(Check(f"{label} chunk count", False, "index manifest has no positive chunk_count"))
    else:
        checks.append(Check(f"{label} chunk count", True, str(chunk_count)))
    if chunks_required or chunks_text.strip():
        _check_chunks(chunks_text, f"{label} chunks", chunk_count or None, checks)

    # PersistentClient currently writes chroma.sqlite3.  Requiring at least one
    # store file also catches the case where someone copied only index_manifest.
    store_files = [item for item in index_dir.iterdir() if item.is_file() and item.name != "index_manifest.json"]
    if not store_files:
        checks.append(Check(f"{label} vector store", False, f"no Chroma store files in {index_dir}"))
    else:
        checks.append(Check(f"{label} vector store", True, str(index_dir)))
    if check_chroma:
        try:
            import chromadb  # type: ignore[import-not-found]

            client = chromadb.PersistentClient(path=str(index_dir))
            collection_object = client.get_collection(
                name=collection,
                embedding_function=None,
            )
            actual_count = int(collection_object.count())
        except Exception as exc:
            checks.append(Check(f"{label} Chroma collection", False, f"cannot open {collection!r}: {exc}"))
        else:
            if actual_count != chunk_count:
                checks.append(
                    Check(
                        f"{label} Chroma collection",
                        False,
                        f"collection count {actual_count} does not match index manifest {chunk_count}",
                    )
                )
            else:
                checks.append(Check(f"{label} Chroma collection", True, f"{collection} ({actual_count})"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    repo_default = Path(__file__).resolve().parents[2]
    parser.add_argument("--repo-root", type=Path, default=repo_default)
    parser.add_argument("--qwen-model-path", default="")
    parser.add_argument("--embedding-model-path", default="")
    parser.add_argument("--traffic-index-dir", default="")
    parser.add_argument("--traffic-manifest", default="")
    parser.add_argument("--traffic-chunks", default="")
    parser.add_argument("--traffic-collection", default="citypulse_traffic_knowledge")
    parser.add_argument("--standards-index-dir", default="")
    parser.add_argument("--standards-manifest", default="")
    parser.add_argument("--standards-chunks", default="")
    parser.add_argument("--standards-collection", default="citypulse_standards_policy")
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--embedding-dimension", type=int, default=DEFAULT_EMBEDDING_DIMENSION)
    parser.add_argument(
        "--check-dependencies",
        action="store_true",
        help="Check the Python modules required by Qwen, Backend and Chroma",
    )
    parser.add_argument(
        "--require-cuda",
        action="store_true",
        help="Require a CUDA-capable PyTorch runtime for the local Qwen service",
    )
    parser.add_argument(
        "--check-chroma",
        action="store_true",
        help="Open each configured Chroma collection and compare its count",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.repo_root.expanduser().resolve()
    checks: list[Check] = []

    _check_model_dir(args.qwen_model_path, "Qwen model", checks)
    _check_model_dir(args.embedding_model_path, "Embedding model", checks)
    if args.check_dependencies:
        required_modules = (
            "torch",
            "transformers",
            "fastapi",
            "uvicorn",
            "chromadb",
            "sentence_transformers",
        )
        missing_modules = [
            name for name in required_modules if importlib.util.find_spec(name) is None
        ]
        if missing_modules:
            checks.append(Check("Python runtime dependencies", False, ", ".join(missing_modules)))
        else:
            checks.append(Check("Python runtime dependencies", True, ", ".join(required_modules)))
    if args.require_cuda:
        try:
            import torch  # type: ignore[import-not-found]

            cuda_available = bool(torch.cuda.is_available())
            if cuda_available:
                device_count = int(torch.cuda.device_count())
                device_name = torch.cuda.get_device_name(0)
                checks.append(
                    Check("CUDA runtime", True, f"{device_name} ({device_count} device(s))")
                )
            else:
                checks.append(Check("CUDA runtime", False, "torch.cuda.is_available() is false"))
        except Exception as exc:
            checks.append(Check("CUDA runtime", False, f"PyTorch CUDA check failed: {exc}"))
    _check_index(
        label="Traffic RAG",
        index_dir_text=args.traffic_index_dir,
        source_manifest_text=args.traffic_manifest,
        collection=args.traffic_collection,
        embedding_model=args.embedding_model,
        embedding_dimension=args.embedding_dimension,
        chunks_text=args.traffic_chunks,
        chunks_required=True,
        check_chroma=args.check_chroma,
        root=root,
        checks=checks,
    )
    _check_index(
        label="Standards RAG",
        index_dir_text=args.standards_index_dir,
        source_manifest_text=args.standards_manifest,
        collection=args.standards_collection,
        embedding_model=args.embedding_model,
        embedding_dimension=args.embedding_dimension,
        chunks_text=args.standards_chunks,
        chunks_required=False,
        check_chroma=args.check_chroma,
        root=root,
        checks=checks,
    )

    ok = all(item.ok for item in checks)
    if args.json:
        print(
            json.dumps(
                {"ok": ok, "checks": [item.__dict__ for item in checks]},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for item in checks:
            marker = "OK" if item.ok else "FAIL"
            print(f"[{marker}] {item.label}: {item.detail}")
        if not ok:
            print("[FAIL] Copilot preflight failed; services were not started.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
