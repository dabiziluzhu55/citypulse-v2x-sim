#!/usr/bin/env python3
"""Build the CityPulse traffic knowledge chunks and Chroma index.

Examples:

    python scripts/rag/build_knowledge_index.py --chunks-only
    python scripts/rag/build_knowledge_index.py \
        --embedding-model-path /models/Qwen3-Embedding-0.6B

The source Markdown and manifest remain the source of truth.  The generated
``chunks.jsonl`` is reviewable; model weights and the persistent Chroma
directory are deployment artifacts and should not be committed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.copilot.rag import (  # noqa: E402
    DEFAULT_BATCH_SIZE,
    DEFAULT_COLLECTION_NAME,
    DEFAULT_EMBEDDING_MODEL,
    build_chroma_index,
    build_knowledge_chunks,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPOSITORY_ROOT / "traffic_knowledge" / "manifest.json",
        help="Path to traffic_knowledge/manifest.json",
    )
    parser.add_argument(
        "--chunks-output",
        type=Path,
        default=REPOSITORY_ROOT / "traffic_knowledge" / "build" / "chunks.jsonl",
        help="Reviewable JSONL chunk output path",
    )
    parser.add_argument(
        "--index-dir",
        type=Path,
        default=REPOSITORY_ROOT / "outputs" / "rag" / "chroma",
        help="Persistent Chroma directory",
    )
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION_NAME,
        help="Chroma collection name",
    )
    parser.add_argument(
        "--embedding-model",
        default=DEFAULT_EMBEDDING_MODEL,
        help="Embedding model id recorded in the index manifest",
    )
    parser.add_argument(
        "--embedding-model-path",
        type=Path,
        default=None,
        help="Optional local model directory; avoids downloading at runtime",
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
    parser.add_argument(
        "--chunks-only",
        action="store_true",
        help="Only generate chunks.jsonl; skip optional ML/Chroma dependencies",
    )
    return parser.parse_args()


def write_chunks(path: Path, chunks: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def main() -> int:
    args = parse_args()
    manifest = args.manifest.resolve()
    chunks = build_knowledge_chunks(manifest)
    write_chunks(args.chunks_output.resolve(), chunks)
    print(f"Generated {len(chunks)} chunks: {args.chunks_output.resolve()}")
    if args.chunks_only:
        return 0

    metadata = build_chroma_index(
        chunks,
        index_dir=args.index_dir,
        knowledge_manifest_path=manifest,
        embedding_model=args.embedding_model,
        embedding_model_path=args.embedding_model_path,
        device=args.device,
        collection_name=args.collection,
        batch_size=args.batch_size,
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
