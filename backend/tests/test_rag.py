"""RAG chunking and TrafficToolService integration tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.app.copilot.rag import (
    AI_EVALUATION_DOCUMENT_ID,
    CANONICAL_EFFICIENCY_DOCUMENT_ID,
    ChromaKnowledgeRetriever,
    CompositeKnowledgeRetriever,
    CONTROL_PROFILE,
    POLICY_KNOWLEDGE_SOURCE,
    KnowledgeQuery,
    KnowledgeResult,
    KnowledgeSearchResponse,
    SAFETY_METRIC_DOCUMENT_ID,
    STANDARDS_KNOWLEDGE_SOURCE,
    KnowledgeUnavailableError,
    build_knowledge_chunks,
    route_knowledge_query,
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

    efficiency_chunks = [
        item for item in chunks if item["document_id"] == CANONICAL_EFFICIENCY_DOCUMENT_ID
    ]
    assert efficiency_chunks
    assert all(
        item["document_role"] == "canonical_metric_definition"
        and item["status"] == "current"
        and item["profile_control"]
        for item in efficiency_chunks
    )

    evaluation_chunks = [
        item for item in chunks if item["document_id"] == AI_EVALUATION_DOCUMENT_ID
    ]
    assert evaluation_chunks
    assert all(
        item["document_role"] == "evaluation_protocol"
        and not item["profile_control"]
        and item["profile_general"]
        for item in evaluation_chunks
    )


@pytest.mark.parametrize(
    ("query", "sources", "document_ids", "reason"),
    [
        (
            "当前正式指标 TTI 怎么计算",
            ("traffic",),
            (CANONICAL_EFFICIENCY_DOCUMENT_ID,),
            "project_metric",
        ),
        (
            "国家标准中的交通运行状态如何分级",
            (STANDARDS_KNOWLEDGE_SOURCE,),
            (),
            "standards",
        ),
        (
            "项目当前指标和国家标准是否一致",
            ("traffic", STANDARDS_KNOWLEDGE_SOURCE),
            (CANONICAL_EFFICIENCY_DOCUMENT_ID,),
            "project_vs_standard",
        ),
        (
            "AI 管控如何进行公平对照实验",
            ("traffic",),
            (AI_EVALUATION_DOCUMENT_ID,),
            "ai_evaluation",
        ),
        (
            "急刹率的项目定义",
            ("traffic",),
            (SAFETY_METRIC_DOCUMENT_ID,),
            "project_metric",
        ),
    ],
)
def test_knowledge_routing_separates_metric_standard_and_ai_evaluation(
    query: str,
    sources: tuple[str, ...],
    document_ids: tuple[str, ...],
    reason: str,
) -> None:
    routing = route_knowledge_query(query, profile=CONTROL_PROFILE)

    assert routing.knowledge_sources == sources
    assert routing.document_ids == document_ids
    assert routing.reason == reason


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


class _StaticRetriever:
    def __init__(self, response: KnowledgeSearchResponse) -> None:
        self.response = response
        self.requests: list[KnowledgeQuery] = []

    def search(self, request: KnowledgeQuery) -> KnowledgeSearchResponse:
        self.requests.append(request)
        return self.response


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


class _FakeStandardsCollection:
    def __init__(self) -> None:
        self.queries: list[dict[str, Any]] = []

    def query(self, **kwargs: Any) -> dict[str, Any]:
        self.queries.append(kwargs)
        return {
            "ids": [["standard-metric-1"]],
            "documents": [["交通运行评价指标定义与计算方法"]],
            "metadatas": [
                [
                    {
                        "document_id": "gb-t-33171-2016",
                        "source_path": "standards/gb_t_33171_2016.md",
                        "source_pdf": "standards/gb_t_33171_2016.pdf",
                        "title": "城市交通运行状况评价规范",
                        "profile": "standards",
                        "standard_number": "GB/T 33171-2016",
                        "authority": "国家标准",
                        "status": "published",
                        "document_status": "现行",
                        "information_type": "evaluation_metric",
                        "chapter": "第5章 评价指标",
                        "clause": "5.2",
                        "source_page": 18,
                        "printed_page": 16,
                        "page_mapping_status": "verified",
                        "knowledge_version": "standards-v1",
                    }
                ]
            ],
            "distances": [[0.08]],
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


@pytest.mark.parametrize(
    ("query", "model_sources", "expected_sources", "expected_documents", "expected_profile"),
    [
        (
            "当前正式指标 TTI 怎么计算",
            [STANDARDS_KNOWLEDGE_SOURCE],
            ("traffic",),
            (CANONICAL_EFFICIENCY_DOCUMENT_ID,),
            CONTROL_PROFILE,
        ),
        (
            "国家标准中的交通运行状态如何分级",
            ["traffic"],
            (STANDARDS_KNOWLEDGE_SOURCE,),
            (),
            CONTROL_PROFILE,
        ),
        (
            "项目指标和国家标准是否一致",
            [],
            ("traffic", STANDARDS_KNOWLEDGE_SOURCE),
            (CANONICAL_EFFICIENCY_DOCUMENT_ID,),
            CONTROL_PROFILE,
        ),
        (
            "AI 管控如何进行公平对照实验",
            [],
            ("traffic",),
            (AI_EVALUATION_DOCUMENT_ID,),
            "general",
        ),
    ],
)
def test_search_knowledge_backend_overrides_model_source_hint(
    query: str,
    model_sources: list[str],
    expected_sources: tuple[str, ...],
    expected_documents: tuple[str, ...],
    expected_profile: str,
) -> None:
    retriever = _Retriever()
    service = TrafficToolService(
        InMemoryTrafficDataSource({"session-1": [_snapshot()]}),
        session_id="session-1",
        knowledge_retriever=retriever,
    )

    result = service.execute(
        "search_knowledge",
        {
            "query": query,
            "profile": CONTROL_PROFILE,
            "knowledge_sources": model_sources,
        },
    )

    request = retriever.requests[0]
    assert request.knowledge_sources == expected_sources
    assert request.document_ids == expected_documents
    assert request.profile == expected_profile
    assert result["data"]["routing"]["reason"] in {
        "project_metric",
        "standards",
        "project_vs_standard",
        "ai_evaluation",
    }


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


def test_chroma_retriever_can_restrict_to_canonical_document() -> None:
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

    retriever.search(
        KnowledgeQuery(
            "当前正式指标",
            profile=CONTROL_PROFILE,
            knowledge_sources=("traffic",),
            document_ids=(CANONICAL_EFFICIENCY_DOCUMENT_ID,),
        )
    )

    where = collection.queries[0]["where"]
    assert where["$and"] == [
        {"status": "current"},
        {"profile_control": True},
        {"document_id": CANONICAL_EFFICIENCY_DOCUMENT_ID},
    ]


def test_knowledge_query_validates_and_normalizes_knowledge_sources() -> None:
    request = KnowledgeQuery(
        "正式评价指标",
        knowledge_sources=("STANDARDS", "standards", ""),
    )
    assert request.knowledge_sources == ("standards",)

    with pytest.raises(ValueError, match="profile='general'"):
        KnowledgeQuery(
            "雄安规划",
            profile=CONTROL_PROFILE,
            knowledge_sources=(POLICY_KNOWLEDGE_SOURCE,),
        )


def test_standards_retriever_applies_filters_and_returns_provenance() -> None:
    collection = _FakeStandardsCollection()
    retriever = ChromaKnowledgeRetriever(
        index_dir="outputs/rag/standards_policy_chroma",
        knowledge_manifest_path="outputs/rag/standards/rag_manifest.json",
        collection_name="citypulse_standards_policy",
        index_kind=STANDARDS_KNOWLEDGE_SOURCE,
    )
    retriever._collection = collection
    retriever._model = _FakeEmbeddingModel()
    retriever._index_metadata = {
        "knowledge_version": "standards-v1",
        "code_revision": "rev-standards",
        "embedding_model": "Qwen/Qwen3-Embedding-0.6B",
        "embedding_dimension": 1024,
        "collection_name": "citypulse_standards_policy",
    }

    response = retriever.search(
        KnowledgeQuery(
            "正式评价指标的定义",
            limit=2,
            profile="general",
            information_types=("evaluation_metric",),
            knowledge_sources=(STANDARDS_KNOWLEDGE_SOURCE,),
        )
    )

    assert collection.queries[0]["where"] == {
        "$and": [
            {"profile": "standards"},
            {
                "$or": [
                    {"status": "published"},
                    {"status": "draft_for_approval"},
                    {"status": "planning_reference"},
                ]
            },
            {"information_type": "evaluation_metric"},
        ]
    }
    result = response.results[0].as_dict()
    assert result["standard_number"] == "GB/T 33171-2016"
    assert result["source_page"] == 18
    assert result["printed_page"] == 16
    assert result["profile"] == "standards"


def test_standalone_retriever_rejects_an_unconfigured_knowledge_source() -> None:
    retriever = ChromaKnowledgeRetriever(
        index_dir="outputs/rag/traffic_knowledge_chroma",
        knowledge_manifest_path="traffic_knowledge/manifest.json",
    )

    with pytest.raises(KnowledgeUnavailableError, match="Standards/policy"):
        retriever.search(
            KnowledgeQuery(
                "正式评价指标",
                knowledge_sources=(STANDARDS_KNOWLEDGE_SOURCE,),
            )
        )


def test_composite_retriever_merges_sources_and_preserves_index_metadata() -> None:
    traffic = _StaticRetriever(
        KnowledgeSearchResponse(
            results=(
                KnowledgeResult(
                    chunk_id="traffic-1",
                    text="交通知识",
                    metadata={"source_path": "traffic/event.md"},
                    distance=0.30,
                ),
            ),
            index_metadata={"knowledge_version": "traffic-v1"},
        )
    )
    standards = _StaticRetriever(
        KnowledgeSearchResponse(
            results=(
                KnowledgeResult(
                    chunk_id="standard-1",
                    text="国家标准",
                    metadata={
                        "profile": "standards",
                        "standard_number": "GB/T 33171-2016",
                    },
                    distance=0.10,
                ),
            ),
            index_metadata={"knowledge_version": "standards-v1"},
        )
    )
    retriever = CompositeKnowledgeRetriever(traffic, standards)

    response = retriever.search(
        KnowledgeQuery(
            "交通评价指标",
            limit=2,
            knowledge_sources=("traffic", STANDARDS_KNOWLEDGE_SOURCE),
        )
    )

    assert [item.chunk_id for item in response.results] == [
        "standard-1",
        "traffic-1",
    ]
    assert response.results[0].metadata["knowledge_source"] == "standards"
    assert response.results[1].metadata["knowledge_source"] == "traffic"
    assert response.search_mode == "vector_multi"
    assert response.index_metadata["knowledge_sources"] == ["traffic", "standards"]
    assert set(response.index_metadata["indexes"]) == {"traffic", "standards"}
    assert traffic.requests[0].knowledge_sources == ("traffic",)
    assert standards.requests[0].knowledge_sources == (STANDARDS_KNOWLEDGE_SOURCE,)

    traffic_only = retriever.search(
        KnowledgeQuery("交通处置", knowledge_sources=("traffic",))
    )
    assert [item.chunk_id for item in traffic_only.results] == ["traffic-1"]
    assert len(traffic.requests) == 2
    assert len(standards.requests) == 1


def test_composite_comparison_preserves_each_requested_source() -> None:
    traffic = _StaticRetriever(
        KnowledgeSearchResponse(
            results=(
                KnowledgeResult(
                    chunk_id="traffic-metric",
                    text="项目当前指标定义",
                    metadata={"knowledge_source": "traffic"},
                    distance=0.40,
                ),
            ),
            index_metadata={"knowledge_version": "traffic-v1"},
        )
    )
    standards = _StaticRetriever(
        KnowledgeSearchResponse(
            results=tuple(
                KnowledgeResult(
                    chunk_id=f"standard-{index}",
                    text="国家标准指标定义",
                    metadata={"knowledge_source": "standards"},
                    distance=0.01 * index,
                )
                for index in range(1, 6)
            ),
            index_metadata={"knowledge_version": "standards-v1"},
        )
    )

    response = CompositeKnowledgeRetriever(traffic, standards).search(
        KnowledgeQuery(
            "项目指标与国家标准比较",
            limit=3,
            knowledge_sources=("traffic", STANDARDS_KNOWLEDGE_SOURCE),
        )
    )

    assert len(response.results) == 3
    assert {item.metadata["knowledge_source"] for item in response.results} == {
        "traffic",
        "standards",
    }
    assert response.results[0].chunk_id == "standard-1"
    assert response.results[-1].chunk_id == "traffic-metric"


def test_composite_canonical_metric_comparison_puts_project_definition_first() -> None:
    traffic = _StaticRetriever(
        KnowledgeSearchResponse(
            results=(
                KnowledgeResult(
                    chunk_id="traffic-metric",
                    text="项目当前正式指标定义",
                    metadata={"knowledge_source": "traffic"},
                    distance=0.40,
                ),
            ),
            index_metadata={"knowledge_version": "traffic-v1"},
        )
    )
    standards = _StaticRetriever(
        KnowledgeSearchResponse(
            results=(
                KnowledgeResult(
                    chunk_id="standard-metric",
                    text="国家标准指标定义",
                    metadata={"knowledge_source": "standards"},
                    distance=0.01,
                ),
            ),
            index_metadata={"knowledge_version": "standards-v1"},
        )
    )

    response = CompositeKnowledgeRetriever(traffic, standards).search(
        KnowledgeQuery(
            "项目当前正式指标与国家标准的对应关系",
            limit=2,
            knowledge_sources=("traffic", STANDARDS_KNOWLEDGE_SOURCE),
            document_ids=(CANONICAL_EFFICIENCY_DOCUMENT_ID,),
        )
    )

    assert [item.chunk_id for item in response.results] == [
        "traffic-metric",
        "standard-metric",
    ]


def test_composite_retriever_defaults_to_project_knowledge_only() -> None:
    traffic = _StaticRetriever(
        KnowledgeSearchResponse(
            results=(
                KnowledgeResult(
                    chunk_id="traffic-1",
                    text="交通知识",
                    metadata={},
                    distance=0.1,
                ),
            ),
            index_metadata={"knowledge_version": "traffic-v1"},
        )
    )
    standards = _StaticRetriever(
        KnowledgeSearchResponse(
            results=(),
            index_metadata={"knowledge_version": "standards-v1"},
        )
    )

    response = CompositeKnowledgeRetriever(traffic, standards).search(
        KnowledgeQuery("普通交通知识")
    )

    assert [item.chunk_id for item in response.results] == ["traffic-1"]
    assert len(traffic.requests) == 1
    assert not standards.requests


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
        "knowledge_sources",
    } <= set(definition["parameters"]["properties"])
