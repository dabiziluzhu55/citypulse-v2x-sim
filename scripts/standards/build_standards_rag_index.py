#!/usr/bin/env python3
"""Build a persistent Chroma index for the standards/policy source set.

This builder deliberately accepts the reviewable ``chunks.jsonl`` produced by
``build_standards_rag_sources.py``.  It does not read PDFs, download models,
or modify the regular ``traffic_knowledge`` index.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


DEFAULT_MODEL = "Qwen/Qwen3-Embedding-0.6B"
DEFAULT_DIMENSION = 1024
DEFAULT_COLLECTION = "citypulse_standards_policy"
DEFAULT_BATCH_SIZE = 16


class BuildError(RuntimeError):
    """Raised when the source or index contract is invalid."""


def parse_args() -> argparse.Namespace:
    script_root = Path(__file__).resolve().parents[2]
    default_source_root = (
        script_root.parent / "国家与行业标准文件" / "MinerU-normalized"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--chunks",
        type=Path,
        default=default_source_root / "rag_build" / "chunks.jsonl",
        help="Reviewable standards/policy chunks JSONL",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=default_source_root / "rag_manifest.json",
        help="Standards/policy RAG manifest",
    )
    parser.add_argument(
        "--index-dir",
        type=Path,
        default=script_root / "outputs" / "rag" / "standards_policy_chroma",
        help="Persistent Chroma directory",
    )
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION,
        help="Chroma collection name",
    )
    parser.add_argument(
        "--embedding-model",
        default=DEFAULT_MODEL,
        help="Embedding model id recorded in the index manifest",
    )
    parser.add_argument(
        "--embedding-model-path",
        type=Path,
        required=True,
        help="Existing local Embedding model directory; no download is attempted",
    )
    parser.add_argument(
        "--device",
        choices=("auto", "cpu", "cuda"),
        default="auto",
        help="Embedding device",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="Embedding batch size",
    )
    return parser.parse_args()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"Cannot read JSON file: {path}") from exc
    if not isinstance(payload, dict):
        raise BuildError(f"JSON root must be an object: {path}")
    return payload


def _read_chunks(path: Path) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise BuildError(f"Cannot read chunks: {path}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BuildError(f"Invalid chunk JSON at {path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise BuildError(f"Chunk must be an object at {path}:{line_number}")
        chunks.append(value)
    if not chunks:
        raise BuildError(f"No chunks found: {path}")
    return chunks


def _resolve_device(device: str) -> str:
    normalized = str(device).strip().lower() or "auto"
    if normalized in {"cpu", "cuda"}:
        if normalized == "cuda":
            try:
                import torch
            except ImportError as exc:
                raise BuildError("cuda requested but torch is unavailable") from exc
            if not torch.cuda.is_available():
                raise BuildError("cuda requested but CUDA is unavailable")
        return normalized
    if normalized != "auto":
        raise BuildError("device must be auto, cpu, or cuda")
    try:
        import torch
    except ImportError:
        return "cpu"
    return "cuda" if torch.cuda.is_available() else "cpu"


def _as_metadata_value(value: Any) -> str | int | float | bool | None:
    """Convert JSONL metadata to Chroma's scalar metadata contract."""

    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, str)):
        return value
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


_METADATA_KEYS = (
    "document_id",
    "source_path",
    "source_pdf",
    "source_json",
    "title",
    "standard_number",
    "document_number",
    "authority",
    "status",
    "document_status",
    "profile",
    "retrieval_profiles",
    "information_type",
    "priority",
    "sources",
    "applicable_events",
    "applicable_presets",
    "knowledge_version",
    "code_revision",
    "section",
    "section_path",
    "chapter",
    "clause",
    "section_number",
    "chunk_type",
    "table_caption",
    "table_row_start",
    "table_row_end",
    "source_page",
    "source_pages",
    "printed_page",
    "printed_pages",
    "source_page_basis",
    "page_mapping_status",
    "page_mapping_method",
    "page_mapping_confidence",
    "section_mapping_method",
)


def _metadata(chunk: Mapping[str, Any]) -> dict[str, str | int | float | bool]:
    result: dict[str, str | int | float | bool] = {}
    for key in _METADATA_KEYS:
        if key not in chunk:
            continue
        value = _as_metadata_value(chunk[key])
        if value is not None:
            result[key] = value
    return result


def _embedding_rows(values: Any) -> list[list[float]]:
    if hasattr(values, "tolist"):
        values = values.tolist()
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise BuildError("Embedding model returned an invalid value")
    rows: list[list[float]] = []
    for row in values:
        if hasattr(row, "tolist"):
            row = row.tolist()
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)):
            raise BuildError("Embedding model returned an invalid row")
        rows.append([float(item) for item in row])
    return rows


def _write_json_last(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def build_index(args: argparse.Namespace) -> dict[str, Any]:
    if args.batch_size < 1:
        raise BuildError("batch_size must be positive")

    chunks_path = args.chunks.resolve()
    manifest_path = args.manifest.resolve()
    model_path = args.embedding_model_path.resolve()
    index_dir = args.index_dir.resolve()
    if not model_path.is_dir():
        raise BuildError(f"Embedding model directory does not exist: {model_path}")
    manifest = _read_json(manifest_path)
    chunks = _read_chunks(chunks_path)
    knowledge_version = str(manifest.get("version", "")).strip()
    if not knowledge_version:
        raise BuildError("Manifest version is empty")
    code_revision = str(manifest.get("code_revision", "")).strip()
    if not code_revision:
        code_revision = str(manifest.get("project_revision", "")).strip()
    if not code_revision:
        code_revision = "unknown"

    ids = [str(item.get("chunk_id", "")).strip() for item in chunks]
    texts = [str(item.get("text", "")).strip() for item in chunks]
    if any(not item for item in ids) or len(set(ids)) != len(ids):
        raise BuildError("Chunks must have unique non-empty chunk_id values")
    if any(not item for item in texts):
        raise BuildError("Every chunk must contain non-empty text")
    for chunk in chunks:
        if str(chunk.get("knowledge_version", knowledge_version)) != knowledge_version:
            raise BuildError("Chunk knowledge_version does not match manifest version")

    try:
        import chromadb
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise BuildError("Install chromadb and sentence-transformers first") from exc

    resolved_device = _resolve_device(args.device)
    try:
        model = SentenceTransformer(str(model_path), device=resolved_device)
        embeddings = model.encode(
            texts,
            batch_size=args.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=True,
        )
    except Exception as exc:
        raise BuildError(f"Embedding failed with model {model_path}") from exc
    embedding_rows = _embedding_rows(embeddings)
    dimension = len(embedding_rows[0]) if embedding_rows else 0
    if dimension != DEFAULT_DIMENSION or any(
        len(row) != dimension for row in embedding_rows
    ):
        raise BuildError(
            f"Expected {DEFAULT_DIMENSION}-dimensional embeddings, got {dimension}"
        )

    index_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(index_dir))
    collection_name = str(args.collection).strip() or DEFAULT_COLLECTION
    collection = client.get_or_create_collection(
        name=collection_name,
        embedding_function=None,
        metadata={"hnsw:space": "cosine"},
    )
    existing = collection.get(include=["metadatas"])
    existing_ids = existing.get("ids", []) if isinstance(existing, Mapping) else []
    current_ids = set(ids)
    stale_ids = [str(item) for item in existing_ids if str(item) not in current_ids]
    if stale_ids:
        collection.delete(ids=stale_ids)
    collection.upsert(
        ids=ids,
        documents=texts,
        embeddings=embedding_rows,
        metadatas=[_metadata(item) for item in chunks],
    )

    index_metadata = {
        "knowledge_version": knowledge_version,
        "code_revision": code_revision,
        "embedding_model": str(args.embedding_model).strip() or DEFAULT_MODEL,
        "embedding_model_path": str(model_path),
        "embedding_dimension": dimension,
        "collection_name": collection_name,
        "chunk_count": len(chunks),
        "device": resolved_device,
        "source_manifest": str(manifest_path),
        "source_chunks": str(chunks_path),
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json_last(index_dir / "index_manifest.json", index_metadata)
    return index_metadata


def main() -> int:
    args = parse_args()
    metadata = build_index(args)
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
