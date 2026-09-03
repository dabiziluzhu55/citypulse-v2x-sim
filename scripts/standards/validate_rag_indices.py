#!/usr/bin/env python3
"""Run read-only retrieval smoke tests against the two CityPulse indexes."""

from __future__ import annotations

import argparse
from pathlib import Path
import json
import sys
from typing import Any


DEFAULT_QUERY_INSTRUCTION = (
    "Given a traffic control or traffic engineering question, retrieve "
    "relevant passages that help answer the question."
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traffic-index-dir", type=Path, required=True)
    parser.add_argument("--traffic-collection", default="citypulse_traffic_knowledge")
    parser.add_argument("--standards-index-dir", type=Path, required=True)
    parser.add_argument("--standards-collection", default="citypulse_standards_policy")
    parser.add_argument("--embedding-model-path", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument("--top-k", type=int, default=3)
    return parser.parse_args()


def _query(collection: Any, model: Any, query: str, top_k: int) -> list[dict[str, Any]]:
    query_text = f"Instruct: {DEFAULT_QUERY_INSTRUCTION}\nQuery: {query}"
    vector = model.encode(
        [query_text],
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    raw = collection.query(
        query_embeddings=[vector[0].tolist()],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )
    documents = (raw.get("documents") or [[]])[0]
    metadatas = (raw.get("metadatas") or [[]])[0]
    distances = (raw.get("distances") or [[]])[0]
    results: list[dict[str, Any]] = []
    for document, metadata, distance in zip(documents, metadatas, distances):
        item = dict(metadata or {})
        item["distance"] = distance
        item["similarity"] = 1.0 - float(distance) if distance is not None else None
        item["snippet"] = str(document).replace("\n", " ")[:180]
        results.append(item)
    return results


def main() -> int:
    args = parse_args()
    if args.top_k < 1:
        raise SystemExit("--top-k must be positive")
    if not args.embedding_model_path.is_dir():
        raise SystemExit(f"Embedding model directory does not exist: {args.embedding_model_path}")

    import chromadb
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(str(args.embedding_model_path), device=args.device)
    specs = (
        (
            "traffic_knowledge",
            args.traffic_index_dir,
            args.traffic_collection,
            (
                "当前交通评价的正式核心指标有哪些？",
                "实时快照中的 queue_length_m 和溢流率用于什么？",
            ),
        ),
        (
            "standards_policy",
            args.standards_index_dir,
            args.standards_collection,
            (
                "城市交通运行状况评价中拥堵等级如何划分？",
                "交通信号控制效益有哪些评价指标？",
                "雄安新区交通规划对绿色交通和公共交通有什么要求？",
            ),
        ),
    )
    report: dict[str, Any] = {"device": args.device, "indexes": {}}
    for name, index_dir, collection_name, queries in specs:
        client = chromadb.PersistentClient(path=str(index_dir))
        collection = client.get_collection(name=collection_name, embedding_function=None)
        index_manifest_path = index_dir / "index_manifest.json"
        index_manifest = json.loads(index_manifest_path.read_text(encoding="utf-8"))
        report["indexes"][name] = {
            "count": collection.count(),
            "index_manifest": index_manifest,
            "queries": [
                {"query": query, "results": _query(collection, model, query, args.top_k)}
                for query in queries
            ],
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
