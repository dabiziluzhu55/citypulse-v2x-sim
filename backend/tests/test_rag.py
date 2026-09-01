"""RAG chunking and TrafficToolService integration tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.app.copilot.rag import (
    ChromaKnowledgeRetriever,
    CONTROL_PROFILE,
    KnowledgeQuery,
    KnowledgeResult,
    KnowledgeSearchResponse,
    KnowledgeUnavailableError,
    build_knowledge_chunks,
)
from backend.app.copilot.llm import AssistantMessage, LLMCompletion, ToolCall
from backend.app.copilot.orchestrator import CopilotOrchestrator
from backend.app.copilot.traffic_tools import (
    InMemoryTrafficDataSource,
    TOOL_DEFINITIONS,
    TrafficToolService,
    ToolDataUnavailableError,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_repository_chunks_are_manifest_aware_and_profiles_are_separated() -> None:
    manifest = REPOSITORY_ROOT / "traffic_knowledge" / "manifest.json"
    chunks = build_knowledge_chunks(manifest)
    assert chunks == build_knowledge_chunks(manifest)

    assert len(chunks) >= 200
    assert len({item["chunk_id"] for item in chunks}) == len(chunks)
    assert all(
        {
            "chunk_id",
            "source_path",
            "title",
            "section",
            "text",
            "information_type",
            "status",
            "applicable_events",
            "applicable_presets",
            "priority",
            "sources",
            "code_revision",
        }
        <= set(item)
        for item in chunks
    )
    assert not any(
        item["source_path"].startswith("tools/")
        or item["source_path"].upper().startswith("KNOWLEDGE_")
        or item["source_path"].endswith("README.md")
        for item in chunks
    )
    control_chunks = [item for item in chunks if item["profile_control"]]
    assert control_chunks
    assert all(
        item["information_type"] in {"traffic_expertise", "project_fact"}
        for item in control_chunks
    )
    assert not any(
        item["source_path"].startswith("02_control_algorithms/")
        for item in control_chunks
    )
    assert not any(
        item["source_path"].startswith("07_project/ai_")
        or item["source_path"] == "07_project/llm_decision_boundaries.md"
        or item["source_path"] == "07_project/rag_retrieval_policy.md"
        for item in control_chunks
    )


def test_document_without_h2_or_h3_is_kept_under_document_title(tmp_path: Path) -> None:
    root = tmp_path / "traffic_knowledge"
    root.mkdir()
    (root / "plain.md").write_text(
        "# Plain document\n\nThis body has no subsection heading.\n",
        encoding="utf-8",
    )
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "version": "test",
                "project_revision": "rev-test",
                "documents": [
                    {
                        "id": "plain",
                        "path": "plain.md",
                        "category": "fundamentals",
                        "title": "Plain document",
                        "information_type": "traffic_expertise",
                        "status": "current",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    chunks = build_knowledge_chunks(root / "manifest.json")
    assert len(chunks) == 1
    assert chunks[0]["section"] == "Plain document"
    assert "no subsection heading" in chunks[0]["text"]


def test_large_markdown_table_splits_on_complete_rows(tmp_path: Path) -> None:
    root = tmp_path / "traffic_knowledge"
    root.mkdir()
    rows = "\n".join(
        f"| demo_{index} | approach_{index} | downstream_{index} |"
        for index in range(1, 25)
    )
    (root / "topology.md").write_text(
        "# Topology\n\n## Intersections\n\n"
        "| intersection | approach | downstream |\n"
        "|---|---|---|\n"
        f"{rows}\n",
        encoding="utf-8",
    )
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "version": "test",
                "project_revision": "rev-test",
                "documents": [
                    {
                        "id": "topology",
                        "path": "topology.md",
                        "category": "project",
                        "title": "Topology",
                        "information_type": "project_fact",
                        "status": "current",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    chunks = build_knowledge_chunks(root / "manifest.json", max_chunk_chars=220)
    assert len(chunks) > 1
    assert all(len(item["text"]) <= 220 for item in chunks)
    combined = "\n".join(item["text"] for item in chunks)
    assert all(f"| demo_{index} | approach_{index} | downstream_{index} |" in combined for index in range(1, 25))
    assert all(
        not line.startswith("| demo_") or line.count("|") == 4
        for chunk in chunks
        for line in chunk["text"].splitlines()
    )


def test_mixed_frontmatter_is_split_and_manifest_conflicts_fail(tmp_path: Path) -> None:
    root = tmp_path / "traffic_knowledge"
    root.mkdir()
    document = root / "event.md"
    document.write_text(
        """---
information_type: mixed
status: current
applicable_events:
  - accident
applicable_presets:
  - east_dense
priority: high
---
# 事件响应

## 处置

【交通专业知识】先判断下游存储和上游排队。

【规划功能】未来才允许 AI takeover。
""",
        encoding="utf-8",
    )
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": "test",
                "project_revision": "rev-test",
                "documents": [
                    {
                        "id": "event",
                        "path": "event.md",
                        "category": "traffic_event",
                        "title": "事件响应",
                        "information_type": "mixed",
                        "status": "current",
                        "applicable_events": ["accident"],
                        "applicable_presets": ["east_dense"],
                        "priority": "high",
                        "sources": ["test"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    chunks = build_knowledge_chunks(manifest)
    assert {item["information_type"] for item in chunks} == {
        "traffic_expertise",
        "planning",
    }
    assert any(item["profile_control"] for item in chunks)
    assert not any(
        item["profile_control"] and item["information_type"] == "planning"
        for item in chunks
    )

    document.write_text(document.read_text(encoding="utf-8").replace("east_dense", "west_dense"), encoding="utf-8")
    with pytest.raises(ValueError, match="Manifest/frontmatter conflict"):
        build_knowledge_chunks(manifest)


class _Retriever:
    def __init__(self) -> None:
        self.requests: list[KnowledgeQuery] = []

    def search(self, request: KnowledgeQuery) -> KnowledgeSearchResponse:
        self.requests.append(request)
        return KnowledgeSearchResponse(
            results=(
                KnowledgeResult(
                    chunk_id="event_response__s001",
                    text="文档：事件响应\n章节：上游限流\n\n保护下游存储。",
                    metadata={
                        "source_path": "01_fundamentals/event_responsive_signal_control.md",
                        "title": "事件响应信号控制",
                        "section": "上游限流",
                        "information_type": "traffic_expertise",
                        "status": "current",
                        "priority": "high",
                        "applicable_events": ["accident"],
                        "applicable_presets": ["east_dense"],
                        "sources": ["project_citypulse"],
                        "code_revision": "rev-test",
                    },
                    distance=0.12,
                ),
            ),
            index_metadata={
                "knowledge_version": "0.4",
                "embedding_model": "Qwen/Qwen3-Embedding-0.6B",
            },
        )


class _FakeEmbeddingModel:
    def encode(self, values: list[str], **_: Any) -> list[list[float]]:
        return [[0.0] * 1024 for _ in values]


class _FakeCollection:
    def __init__(self) -> None:
        self.queries: list[dict[str, Any]] = []

    def query(self, **kwargs: Any) -> dict[str, Any]:
        self.queries.append(kwargs)
        return {
            "ids": [["chunk-1"]],
            "documents": [["事故响应知识"]],
            "metadatas": [
                [
                    {
                        "document_id": "event-response",
                        "source_path": "03_traffic_events/traffic_accident.md",
                        "title": "交通事故",
                        "section": "响应",
                        "information_type": "traffic_expertise",
                        "knowledge_version": "0.4",
                    }
                ]
            ],
            "distances": [[0.12]],
        }


class _ToolThenAnswerProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: Any, *, tools: Any = (), **_: Any) -> LLMCompletion:
        self.calls += 1
        if self.calls == 1:
            return LLMCompletion(
                message=AssistantMessage(
                    tool_calls=(
                        ToolCall(
                            call_id="knowledge-call",
                            name="search_knowledge",
                            arguments={
                                "query": "事故后如何保护下游",
                                "profile": "control",
                            },
                        ),
                    )
                ),
                model="Qwen-test",
            )
        return LLMCompletion(
            message=AssistantMessage(content="应先结合下游存储和上游排队情况制定处置建议。"),
            model="Qwen-test",
        )


def _snapshot() -> dict[str, Any]:
    return {
        "session_id": "session-1",
        "elapsed_seconds": 10.0,
        "intersections": {
            "demo_1": {
                "totals": {"vehicle_count": 1},
                "lanes": {},
            }
        },
        "events": [],
        "event_detection": {"cards": []},
        "traffic_style": {"edges": {}},
    }


def test_search_knowledge_uses_injected_vector_retriever() -> None:
    retriever = _Retriever()
    source = InMemoryTrafficDataSource({"session-1": [_snapshot()]})
    service = TrafficToolService(
        source,
        session_id="session-1",
        knowledge_retriever=retriever,
    )

    result = service.execute(
        "search_knowledge",
        {
            "query": "事故后如何保护下游",
            "profile": CONTROL_PROFILE,
            "event_type": "accident",
            "preset_id": "east_dense",
            "information_types": ["traffic_expertise"],
        },
    )

    assert result["data"]["search_mode"] == "vector"
    assert result["data"]["results"][0]["source_path"].endswith(
        "event_responsive_signal_control.md"
    )
    assert retriever.requests[0].profile == CONTROL_PROFILE
    assert retriever.requests[0].event_type == "accident"
    assert retriever.requests[0].information_types == ("traffic_expertise",)


def test_chroma_retriever_applies_all_metadata_filters_and_returns_version() -> None:
    collection = _FakeCollection()
    retriever = ChromaKnowledgeRetriever(
        index_dir="outputs/rag/chroma",
        knowledge_manifest_path="traffic_knowledge/manifest.json",
    )
    retriever._collection = collection
    retriever._model = _FakeEmbeddingModel()
    retriever._index_metadata = {
        "knowledge_version": "0.4",
        "code_revision": "rev-test",
        "embedding_model": "Qwen/Qwen3-Embedding-0.6B",
        "embedding_dimension": 1024,
        "collection_name": "citypulse_traffic_knowledge",
    }

    response = retriever.search(
        KnowledgeQuery(
            query="事故后如何保护下游",
            limit=2,
            profile=CONTROL_PROFILE,
            event_type="accident",
            preset_id="east_dense",
            information_types=("traffic_expertise",),
        )
    )

    assert len(collection.queries) == 1
    where = collection.queries[0]["where"]
    assert where["$and"] == [
        {"status": "current"},
        {"profile_control": True},
        {"information_type": {"$contains": "traffic_expertise"}},
        {"applicable_events": {"$contains": "accident"}},
        {"applicable_presets": {"$contains": "east_dense"}},
    ]
    result = response.results[0].as_dict()
    assert result["source_path"].endswith("traffic_accident.md")
    assert result["knowledge_version"] == "0.4"
    assert result["similarity"] == 0.88


def test_missing_vector_index_is_explicitly_unavailable(tmp_path: Path) -> None:
    retriever = ChromaKnowledgeRetriever(
        index_dir=tmp_path / "missing-index",
        knowledge_manifest_path=REPOSITORY_ROOT / "traffic_knowledge" / "manifest.json",
    )
    with pytest.raises(KnowledgeUnavailableError, match="not built"):
        retriever.search(KnowledgeQuery("拥堵处置"))


def test_copilot_can_complete_a_knowledge_tool_round_trip() -> None:
    retriever = _Retriever()
    service = TrafficToolService(
        InMemoryTrafficDataSource({"session-1": [_snapshot()]}),
        session_id="session-1",
        knowledge_retriever=retriever,
    )
    response = CopilotOrchestrator(
        _ToolThenAnswerProvider(),
        service,
    ).run("事故后如何保护下游")

    assert response.answer.startswith("应先结合")
    assert response.tool_calls[0].name == "search_knowledge"
    assert retriever.requests[0].profile == CONTROL_PROFILE


def test_empty_production_knowledge_does_not_fall_back_to_keyword() -> None:
    source = InMemoryTrafficDataSource({"session-1": [_snapshot()]})
    service = TrafficToolService(source, session_id="session-1")
    with pytest.raises(ToolDataUnavailableError, match="vector index"):
        service.execute("search_knowledge", {"query": "拥堵处置"})


def test_knowledge_tool_definition_exposes_metadata_filters() -> None:
    definition = next(
        item["function"]
        for item in TOOL_DEFINITIONS
        if item["function"]["name"] == "search_knowledge"
    )
    assert {
        "profile",
        "event_type",
        "preset_id",
        "information_types",
    } <= set(definition["parameters"]["properties"])
