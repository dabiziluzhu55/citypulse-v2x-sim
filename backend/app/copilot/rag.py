"""Manifest-aware knowledge chunking and local Chroma retrieval.

The traffic knowledge directory is deliberately kept as a human-readable
source corpus.  This module provides the deterministic build contract and the
runtime retriever without importing optional ML/vector-store dependencies at
module import time.  That keeps the simulation backend startable when the RAG
artifacts or model packages are not installed; a RAG query then reports an
explicit unavailable error instead of silently falling back to keyword search.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
DEFAULT_EMBEDDING_DIMENSION = 1024
DEFAULT_COLLECTION_NAME = "citypulse_traffic_knowledge"
DEFAULT_QUERY_INSTRUCTION = (
    "Given a traffic control or traffic engineering question, retrieve "
    "relevant passages that help answer the question."
)
DEFAULT_MAX_CHUNK_CHARS = 4_000
DEFAULT_CHUNK_OVERLAP_CHARS = 200
DEFAULT_BATCH_SIZE = 16

CONTROL_PROFILE = "control"
GENERAL_PROFILE = "general"
SUPPORTED_PROFILES = frozenset({CONTROL_PROFILE, GENERAL_PROFILE})
TRAFFIC_KNOWLEDGE_SOURCE = "traffic"
STANDARDS_KNOWLEDGE_SOURCE = "standards"
POLICY_KNOWLEDGE_SOURCE = "policy"
CANONICAL_EFFICIENCY_DOCUMENT_ID = "metrics_efficiency"
SAFETY_METRIC_DOCUMENT_ID = "metrics_safety"
EMISSION_METRIC_DOCUMENT_ID = "metrics_emission"
AI_EVALUATION_DOCUMENT_ID = "metrics_ai_evaluation"
SUPPORTED_KNOWLEDGE_SOURCES = frozenset(
    {
        TRAFFIC_KNOWLEDGE_SOURCE,
        STANDARDS_KNOWLEDGE_SOURCE,
        POLICY_KNOWLEDGE_SOURCE,
    }
)

_LABEL_TO_INFORMATION_TYPE = {
    "项目事实": "project_fact",
    "交通专业知识": "traffic_expertise",
    "规划功能": "planning",
}
_LABEL_PATTERN = re.compile(
    r"(?:\*\*)?【(?P<label>项目事实|交通专业知识|规划功能)】(?:\*\*)?"
)
_HEADING_PATTERN = re.compile(r"^(?P<marks>#{1,6})[ \t]+(?P<title>.+?)\s*$")
_FRONTMATTER_KEY_PATTERN = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_-]*):(?:[ \t]*(?P<value>.*))?$")
_DEFAULT_MIXED_INFORMATION_TYPE = {
    "fundamentals": "traffic_expertise",
    "traffic_event": "traffic_expertise",
    "standard": "traffic_expertise",
    "scenario": "project_fact",
    "metric": "project_fact",
    "project": "project_fact",
    "simulation_case": "project_fact",
}
_CONTROL_EXCLUDED_CATEGORIES = frozenset({"control_algorithm"})
_CONTROL_EXCLUDED_PATH_PARTS = frozenset({"tools"})
_EXCLUDED_FILE_NAMES = frozenset({"README.md"})


class KnowledgeError(RuntimeError):
    """Base error for build or runtime knowledge failures."""


class KnowledgeUnavailableError(KnowledgeError):
    """Raised when the index/model/dependencies cannot serve a query."""


class KnowledgeBuildError(KnowledgeError, ValueError):
    """Raised when source metadata or chunk construction is invalid."""


@dataclass(frozen=True)
class KnowledgeQuery:
    """A validated query shared by Copilot and future AI-control retrieval."""

    query: str
    limit: int = 5
    profile: str = GENERAL_PROFILE
    event_type: str | None = None
    preset_id: str | None = None
    information_types: tuple[str, ...] = ()
    knowledge_sources: tuple[str, ...] = ()
    # Internal backend routing filter.  It is deliberately not exposed as a
    # model tool argument: the wording of the user's question decides which
    # canonical project document may be searched.
    document_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        normalized_query = str(self.query).strip()
        if not normalized_query:
            raise ValueError("query must not be empty")
        if len(normalized_query) > 500:
            raise ValueError("query must be at most 500 characters")
        try:
            normalized_limit = int(self.limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("limit must be an integer") from exc
        if normalized_limit < 1 or normalized_limit > 10:
            raise ValueError("limit must be between 1 and 10")
        normalized_profile = str(self.profile).strip().lower() or GENERAL_PROFILE
        if normalized_profile not in SUPPORTED_PROFILES:
            raise ValueError(
                f"profile must be one of {sorted(SUPPORTED_PROFILES)}"
            )

        normalized_types = tuple(
            dict.fromkeys(
                str(item).strip()
                for item in self.information_types
                if str(item).strip()
            )
        )
        if len(normalized_types) > 4:
            raise ValueError("information_types must contain at most 4 items")

        raw_sources = self.knowledge_sources
        if isinstance(raw_sources, str):
            raw_sources = (raw_sources,)
        normalized_sources = tuple(
            dict.fromkeys(
                str(item).strip().lower()
                for item in (raw_sources or ())
                if str(item).strip()
            )
        )
        unknown_sources = sorted(
            set(normalized_sources) - SUPPORTED_KNOWLEDGE_SOURCES
        )
        if unknown_sources:
            raise ValueError(
                "knowledge_sources must be drawn from "
                f"{sorted(SUPPORTED_KNOWLEDGE_SOURCES)}; got {unknown_sources}"
            )
        if len(normalized_sources) > len(SUPPORTED_KNOWLEDGE_SOURCES):
            raise ValueError("knowledge_sources must contain at most 3 items")
        if normalized_profile == CONTROL_PROFILE and POLICY_KNOWLEDGE_SOURCE in normalized_sources:
            raise ValueError("policy knowledge source requires profile='general'")

        raw_document_ids = self.document_ids
        if isinstance(raw_document_ids, str):
            raw_document_ids = (raw_document_ids,)
        normalized_document_ids = tuple(
            dict.fromkeys(
                str(item).strip()
                for item in (raw_document_ids or ())
                if str(item).strip()
            )
        )
        if len(normalized_document_ids) > 12:
            raise ValueError("document_ids must contain at most 12 items")

        object.__setattr__(self, "query", normalized_query)
        object.__setattr__(self, "limit", normalized_limit)
        object.__setattr__(self, "profile", normalized_profile)
        object.__setattr__(self, "event_type", _optional_text(self.event_type))
        object.__setattr__(self, "preset_id", _optional_text(self.preset_id))
        object.__setattr__(self, "information_types", normalized_types)
        object.__setattr__(self, "knowledge_sources", normalized_sources)
        object.__setattr__(self, "document_ids", normalized_document_ids)


@dataclass(frozen=True)
class KnowledgeResult:
    """One retrieved chunk with provenance and vector similarity information."""

    chunk_id: str
    text: str
    metadata: Mapping[str, Any]
    distance: float | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "distance": self.distance,
            "similarity": _similarity_from_distance(self.distance),
        }
        for key in (
            "document_id",
            "source_path",
            "title",
            "section",
            "knowledge_source",
            "document_role",
            "information_type",
            "document_information_type",
            "status",
            "priority",
            "applicable_events",
            "applicable_presets",
            "sources",
            "code_revision",
            "knowledge_version",
            "retrieval_profiles",
            "profile",
            "standard_number",
            "document_number",
            "authority",
            "chapter",
            "clause",
            "section_path",
            "document_status",
            "chunk_type",
            "source_pdf",
            "source_json",
            "source_page",
            "source_pages",
            "printed_page",
            "printed_pages",
            "page_mapping_status",
            "page_mapping_method",
            "page_mapping_confidence",
        ):
            if key in self.metadata:
                payload[key] = self.metadata[key]
        return payload


@dataclass(frozen=True)
class KnowledgeSearchResponse:
    """Normalized result returned by a knowledge retriever."""

    results: tuple[KnowledgeResult, ...]
    search_mode: str = "vector"
    index_metadata: Mapping[str, Any] = field(default_factory=dict)


class KnowledgeRetriever(Protocol):
    """Runtime retrieval interface used by the Copilot tool layer."""

    def search(self, request: KnowledgeQuery) -> KnowledgeSearchResponse:
        ...


@dataclass(frozen=True)
class KnowledgeRouting:
    """Deterministic source/document routing applied before vector search."""

    profile: str
    knowledge_sources: tuple[str, ...]
    document_ids: tuple[str, ...] = ()
    reason: str = "traffic_knowledge"

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "knowledge_sources": list(self.knowledge_sources),
            "document_ids": list(self.document_ids),
            "reason": self.reason,
        }


_STANDARD_QUERY_MARKERS = (
    "国家标准",
    "行业标准",
    "国家/行业",
    "标准编号",
    "标准条款",
    "标准章节",
    "标准依据",
    "权威依据",
    "现行标准",
    "标准规定",
    "评价标准",
    "条款",
    "gb/t",
    "ga/t",
    "gb ",
)
_PROJECT_QUERY_MARKERS = (
    "项目口径",
    "项目实现",
    "当前系统",
    "当前实现",
    "代码实际",
    "交通评估",
    "traffic_eval",
    "evalresult",
    "本项目",
    "系统采用",
    "仿真实际",
)
_COMPARISON_QUERY_MARKERS = (
    "比较",
    "对比",
    "一致",
    "对应",
    "差异",
    "映射",
    "是否符合",
    "分别",
)
_AI_EVALUATION_QUERY_MARKERS = (
    "ai评估",
    "ai管控评估",
    "ai takeover",
    "takeover",
    "公平对比",
    "对照实验",
    "ai on",
    "ai off",
    "ai开启",
    "ai关闭",
    "fallback",
    "结构化输出",
    "llm推理",
    "接管时长",
)
_METRIC_QUERY_MARKERS = (
    "指标",
    "公式",
    "计算方法",
    "怎么计算",
    "计算口径",
    "阈值",
    "tti",
    "travel time index",
    "行程时间比",
    "延误时间比",
    "平均速度",
    "排队",
    "溢流",
    "吞吐",
    "完成率",
    "停车次数",
    "交通运行指数",
    "交通状态",
    "metric",
)
_SAFETY_METRIC_MARKERS = (
    "安全指标",
    "急刹",
    "碰撞",
    "ttc",
    "pet",
    "drac",
    "hard braking",
)
_EMISSION_METRIC_MARKERS = (
    "排放",
    "燃油",
    "能源",
    "co2",
    "nox",
    "pm",
    "fuel",
    "emission",
    "energy",
)
_POLICY_QUERY_MARKERS = ("雄安规划", "规划纲要", "雄安新区规划", "规划背景")


def route_knowledge_query(
    query: str,
    *,
    profile: str = GENERAL_PROFILE,
    knowledge_sources: Sequence[str] = (),
    information_types: Sequence[str] = (),
) -> KnowledgeRouting:
    """Choose RAG sources from the question before Qwen can mix documents.

    ``knowledge_sources`` is treated as a hint from the model, not as the
    authority for routing.  Explicit user intent wins: standards questions go
    to the standards index, project-vs-standard comparisons use both indexes,
    and ordinary project metric questions are restricted to the canonical
    metric document.  This prevents a semantically similar evaluation plan
    from competing with the current metric definition.
    """

    normalized_query = str(query).strip().casefold()
    normalized_profile = str(profile).strip().lower() or GENERAL_PROFILE
    if normalized_profile not in SUPPORTED_PROFILES:
        normalized_profile = GENERAL_PROFILE
    raw_sources = (
        (knowledge_sources,)
        if isinstance(knowledge_sources, str)
        else (knowledge_sources or ())
    )
    requested_sources = tuple(
        dict.fromkeys(
            str(item).strip().lower()
            for item in raw_sources
            if str(item).strip()
        )
    )
    information_type_set = {
        str(item).strip().lower()
        for item in (information_types or ())
        if str(item).strip()
    }

    # A model-supplied source is only a hint.  Treating it as user intent
    # would let an incorrect ``standards`` tool argument pull the wrong index
    # for a plain project-metric question.
    has_standard_language = _contains_any(
        normalized_query, _STANDARD_QUERY_MARKERS
    )
    has_project_context = _contains_any(
        normalized_query, _PROJECT_QUERY_MARKERS
    )
    has_comparison_intent = has_standard_language and (
        has_project_context
        or _contains_any(normalized_query, _COMPARISON_QUERY_MARKERS)
    )
    has_ai_evaluation_intent = (
        _contains_any(normalized_query, _AI_EVALUATION_QUERY_MARKERS)
        or "evaluation_protocol" in information_type_set
    )
    has_metric_intent = (
        _contains_any(normalized_query, _METRIC_QUERY_MARKERS)
        or _contains_any(normalized_query, _SAFETY_METRIC_MARKERS)
        or _contains_any(normalized_query, _EMISSION_METRIC_MARKERS)
    )

    if has_comparison_intent:
        document_ids = _metric_document_ids(normalized_query) if has_metric_intent else ()
        if has_ai_evaluation_intent and AI_EVALUATION_DOCUMENT_ID not in document_ids:
            document_ids = (AI_EVALUATION_DOCUMENT_ID, *document_ids)
        return KnowledgeRouting(
            profile=normalized_profile,
            knowledge_sources=(TRAFFIC_KNOWLEDGE_SOURCE, STANDARDS_KNOWLEDGE_SOURCE),
            document_ids=document_ids,
            reason="project_vs_standard",
        )

    if has_standard_language:
        return KnowledgeRouting(
            profile=normalized_profile,
            knowledge_sources=(STANDARDS_KNOWLEDGE_SOURCE,),
            reason="standards",
        )

    if has_ai_evaluation_intent:
        document_ids = [AI_EVALUATION_DOCUMENT_ID]
        if has_metric_intent:
            document_ids.extend(
                item
                for item in _metric_document_ids(normalized_query)
                if item not in document_ids
            )
        return KnowledgeRouting(
            profile=GENERAL_PROFILE,
            knowledge_sources=(TRAFFIC_KNOWLEDGE_SOURCE,),
            document_ids=tuple(document_ids),
            reason="ai_evaluation",
        )

    if _contains_any(normalized_query, _POLICY_QUERY_MARKERS) or POLICY_KNOWLEDGE_SOURCE in requested_sources:
        return KnowledgeRouting(
            profile=GENERAL_PROFILE,
            knowledge_sources=(POLICY_KNOWLEDGE_SOURCE,),
            reason="policy",
        )

    if has_metric_intent:
        return KnowledgeRouting(
            profile=normalized_profile,
            knowledge_sources=(TRAFFIC_KNOWLEDGE_SOURCE,),
            document_ids=_metric_document_ids(normalized_query),
            reason="project_metric",
        )

    if requested_sources:
        return KnowledgeRouting(
            profile=GENERAL_PROFILE if POLICY_KNOWLEDGE_SOURCE in requested_sources else normalized_profile,
            knowledge_sources=requested_sources,
            reason="explicit_source_hint",
        )

    return KnowledgeRouting(
        profile=normalized_profile,
        knowledge_sources=(TRAFFIC_KNOWLEDGE_SOURCE,),
        reason="traffic_knowledge",
    )


def _contains_any(value: str, markers: Sequence[str]) -> bool:
    return any(str(marker).casefold() in value for marker in markers)


def _metric_document_ids(query: str) -> tuple[str, ...]:
    document_ids: list[str] = []
    if _contains_any(query, _SAFETY_METRIC_MARKERS):
        document_ids.append(SAFETY_METRIC_DOCUMENT_ID)
    if _contains_any(query, _EMISSION_METRIC_MARKERS):
        document_ids.append(EMISSION_METRIC_DOCUMENT_ID)
    if not document_ids:
        document_ids.append(CANONICAL_EFFICIENCY_DOCUMENT_ID)
    return tuple(document_ids)


def load_knowledge_manifest(path: str | Path) -> dict[str, Any]:
    """Load and minimally validate the repository knowledge manifest."""

    manifest_path = Path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KnowledgeBuildError(
            f"Cannot read knowledge manifest: {manifest_path}"
        ) from exc
    if not isinstance(payload, dict):
        raise KnowledgeBuildError("Knowledge manifest must be a JSON object")
    documents = payload.get("documents")
    if not isinstance(documents, list):
        raise KnowledgeBuildError("Knowledge manifest must contain documents[]")
    return payload


def build_knowledge_chunks(
    manifest_path: str | Path,
    *,
    knowledge_root: str | Path | None = None,
    max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_CHUNK_OVERLAP_CHARS,
) -> list[dict[str, Any]]:
    """Build deterministic JSON-serializable chunks from manifest documents.

    The function has no ML/vector-store dependency and is therefore suitable
    for CI and local inspection.  It emits both ``general`` and ``control``
    retrieval eligibility in metadata; the same chunk can belong to both
    profiles without duplicating its text in the vector store.
    """

    if max_chunk_chars < 200:
        raise KnowledgeBuildError("max_chunk_chars must be at least 200")
    if overlap_chars < 0 or overlap_chars >= max_chunk_chars:
        raise KnowledgeBuildError(
            "overlap_chars must be non-negative and smaller than max_chunk_chars"
        )

    manifest_file = Path(manifest_path).resolve()
    manifest = load_knowledge_manifest(manifest_file)
    root = (
        Path(knowledge_root).resolve()
        if knowledge_root is not None
        else manifest_file.parent
    )
    manifest_version = str(manifest.get("version", ""))
    manifest_revision = str(manifest.get("project_revision", ""))
    chunks: list[dict[str, Any]] = []
    seen_document_ids: set[str] = set()

    for raw_document in manifest["documents"]:
        if not isinstance(raw_document, Mapping):
            raise KnowledgeBuildError("Every manifest document must be an object")
        document = dict(raw_document)
        document_id = str(document.get("id", "")).strip()
        relative_path = _safe_relative_path(document.get("path"), root)
        if not document_id:
            document_id = relative_path.stem
        if document_id in seen_document_ids:
            raise KnowledgeBuildError(f"Duplicate document id: {document_id}")
        seen_document_ids.add(document_id)

        if _is_excluded_source(relative_path):
            continue
        source_path = root / relative_path
        if not source_path.is_file():
            raise KnowledgeBuildError(f"Manifest document is missing: {relative_path}")

        raw_text = source_path.read_text(encoding="utf-8")
        frontmatter, markdown = _extract_frontmatter(raw_text)
        merged = _merge_document_metadata(
            document,
            frontmatter,
            manifest_version=manifest_version,
            manifest_revision=manifest_revision,
        )
        title = str(merged.get("title", "")).strip() or document_id
        sections = _markdown_sections(markdown, title)
        for section_index, (section, section_text) in enumerate(sections, start=1):
            if _is_source_section(section):
                continue
            for fragment_index, (label_type, fragment) in enumerate(
                _split_labeled_fragments(section_text), start=1
            ):
                if not fragment.strip():
                    continue
                information_type = _information_type_for_fragment(
                    merged, label_type
                )
                profiles = _retrieval_profiles(
                    merged,
                    relative_path=relative_path,
                    information_type=information_type,
                )
                if not profiles:
                    continue
                standalone_text = _standalone_text(title, section, fragment)
                parts = _split_large_text(
                    standalone_text,
                    max_chars=max_chunk_chars,
                    overlap_chars=overlap_chars,
                )
                for part_index, part in enumerate(parts, start=1):
                    chunk_id = _chunk_id(
                        document_id,
                        section_index,
                        section,
                        fragment_index,
                        part_index,
                    )
                    chunk_metadata = _chunk_metadata(
                        merged,
                        relative_path=relative_path,
                        title=title,
                        section=section,
                        information_type=information_type,
                        document_information_type=str(
                            merged.get("information_type", "")
                        ),
                        profiles=profiles,
                        manifest_version=manifest_version,
                        manifest_revision=manifest_revision,
                    )
                    chunks.append(
                        {
                            "chunk_id": chunk_id,
                            "source_path": relative_path.as_posix(),
                            "title": title,
                            "section": section,
                            "text": part,
                            **chunk_metadata,
                        }
                    )

    if not chunks:
        raise KnowledgeBuildError("No eligible knowledge chunks were generated")
    return chunks


def build_chroma_index(
    chunks: Sequence[Mapping[str, Any]],
    *,
    index_dir: str | Path,
    knowledge_manifest_path: str | Path,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    embedding_model_path: str | Path | None = None,
    device: str = "auto",
    collection_name: str = DEFAULT_COLLECTION_NAME,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, Any]:
    """Embed chunks and write a persistent Chroma collection.

    Optional dependencies are imported only when this function is called.  A
    completed ``index_manifest.json`` is written last so the runtime never
    treats a partially-built directory as ready.
    """

    if not chunks:
        raise KnowledgeBuildError("Cannot build an empty knowledge index")
    if batch_size < 1:
        raise KnowledgeBuildError("batch_size must be positive")

    try:
        import chromadb  # type: ignore[import-not-found]
        from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
    except ImportError as exc:
        raise KnowledgeUnavailableError(
            "RAG build requires chromadb and sentence-transformers; "
            "install backend/requirements.txt first."
        ) from exc

    manifest = load_knowledge_manifest(knowledge_manifest_path)
    if any(not isinstance(item, Mapping) for item in chunks):
        raise KnowledgeBuildError("Every knowledge chunk must be an object")
    manifest_version = str(manifest.get("version", ""))
    manifest_revision = str(manifest.get("project_revision", ""))
    texts = [str(item.get("text", "")).strip() for item in chunks]
    if any(not text for text in texts):
        raise KnowledgeBuildError("Every chunk must contain non-empty text")
    ids = [str(item.get("chunk_id", "")).strip() for item in chunks]
    if any(not chunk_id for chunk_id in ids) or len(set(ids)) != len(ids):
        raise KnowledgeBuildError("Knowledge chunks must have unique non-empty chunk_id values")
    for item in chunks:
        if "knowledge_version" in item and str(item["knowledge_version"]) != manifest_version:
            raise KnowledgeBuildError("Chunk knowledge_version does not match the source manifest")
    embedding_model_name = str(embedding_model).strip() or DEFAULT_EMBEDDING_MODEL
    normalized_collection_name = str(collection_name).strip() or DEFAULT_COLLECTION_NAME
    resolved_device = resolve_embedding_device(device)
    model_path_text = str(embedding_model_path).strip() if embedding_model_path else ""
    model_source = model_path_text or embedding_model_name
    try:
        model = SentenceTransformer(model_source, device=resolved_device)
    except Exception as exc:
        raise KnowledgeUnavailableError(
            f"Embedding model could not be loaded: {model_source}"
        ) from exc
    try:
        embeddings = _encode_documents(model, texts, batch_size=batch_size)
    except KnowledgeError:
        raise
    except Exception as exc:
        raise KnowledgeUnavailableError("Embedding model failed to encode knowledge chunks") from exc
    dimension = _embedding_dimension(embeddings)
    if dimension != DEFAULT_EMBEDDING_DIMENSION:
        raise KnowledgeBuildError(
            f"Expected {DEFAULT_EMBEDDING_DIMENSION} embedding dimensions, got {dimension}"
        )

    target_dir = Path(index_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(target_dir))
    collection = client.get_or_create_collection(
        name=normalized_collection_name,
        embedding_function=None,
        metadata={"hnsw:space": "cosine"},
    )

    stale_ids = _stale_collection_ids(collection, ids)
    if stale_ids:
        collection.delete(ids=stale_ids)
    for start in range(0, len(chunks), batch_size):
        end = start + batch_size
        batch_chunks = chunks[start:end]
        collection.upsert(
            ids=ids[start:end],
            documents=texts[start:end],
            embeddings=embeddings[start:end],
            metadatas=[_chroma_metadata(item) for item in batch_chunks],
        )

    index_metadata = {
        "knowledge_version": manifest_version,
        "code_revision": manifest_revision,
        "embedding_model": embedding_model_name,
        "embedding_dimension": dimension,
        "collection_name": normalized_collection_name,
        "chunk_count": len(chunks),
        "device": resolved_device,
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json_last(target_dir / "index_manifest.json", index_metadata)
    return index_metadata


class ChromaKnowledgeRetriever:
    """Lazy local Qwen3-Embedding + persistent Chroma retriever."""

    def __init__(
        self,
        *,
        index_dir: str | Path,
        knowledge_manifest_path: str | Path,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        embedding_model_path: str | Path | None = None,
        device: str = "auto",
        collection_name: str = DEFAULT_COLLECTION_NAME,
        query_instruction: str = DEFAULT_QUERY_INSTRUCTION,
        query_timeout_seconds: float = 30.0,
        index_kind: str = TRAFFIC_KNOWLEDGE_SOURCE,
    ) -> None:
        self.index_dir = Path(index_dir).expanduser().resolve()
        self.knowledge_manifest_path = Path(knowledge_manifest_path).expanduser().resolve()
        self.embedding_model = str(embedding_model).strip() or DEFAULT_EMBEDDING_MODEL
        self.embedding_model_path = (
            Path(embedding_model_path).expanduser().resolve()
            if embedding_model_path
            else None
        )
        self.device = str(device).strip().lower() or "auto"
        self.collection_name = str(collection_name).strip() or DEFAULT_COLLECTION_NAME
        self.query_instruction = str(query_instruction).strip() or DEFAULT_QUERY_INSTRUCTION
        self.query_timeout_seconds = float(query_timeout_seconds)
        if self.query_timeout_seconds <= 0:
            raise ValueError("query_timeout_seconds must be positive")
        self.index_kind = str(index_kind).strip().lower() or TRAFFIC_KNOWLEDGE_SOURCE
        if self.index_kind not in {TRAFFIC_KNOWLEDGE_SOURCE, STANDARDS_KNOWLEDGE_SOURCE}:
            raise ValueError(
                "index_kind must be 'traffic' or 'standards'"
            )
        self._collection: Any = None
        self._model: Any = None
        self._index_metadata: dict[str, Any] | None = None
        self._load_lock = threading.RLock()

    def search(self, request: KnowledgeQuery) -> KnowledgeSearchResponse:
        if not isinstance(request, KnowledgeQuery):
            raise KnowledgeUnavailableError("Knowledge query must be a KnowledgeQuery")
        requested_sources = set(request.knowledge_sources)
        if self.index_kind == TRAFFIC_KNOWLEDGE_SOURCE and requested_sources - {
            TRAFFIC_KNOWLEDGE_SOURCE
        }:
            raise KnowledgeUnavailableError(
                "Standards/policy knowledge index is not configured for this retriever."
            )
        if self.index_kind == STANDARDS_KNOWLEDGE_SOURCE and requested_sources - {
            STANDARDS_KNOWLEDGE_SOURCE,
            POLICY_KNOWLEDGE_SOURCE,
        }:
            raise KnowledgeUnavailableError(
                "Traffic knowledge index is not configured for this retriever."
            )
        collection, model, index_metadata = self._ensure_ready()
        started_at = time.monotonic()
        query_text = f"Instruct: {self.query_instruction}\nQuery: {request.query}"
        try:
            query_embedding = _encode_query(model, query_text)
        except KnowledgeError:
            raise
        except Exception as exc:
            raise KnowledgeUnavailableError("Embedding model failed to encode the query") from exc
        _check_query_timeout(started_at, self.query_timeout_seconds)
        candidate_count = min(max(request.limit * 2, request.limit), 50)
        filtered_where = self._where_for_request(request)
        if self.index_kind == TRAFFIC_KNOWLEDGE_SOURCE:
            if request.event_type:
                filtered_where = _add_where_contains(
                    filtered_where, "applicable_events", (request.event_type,)
                )
            if request.preset_id:
                filtered_where = _add_where_contains(
                    filtered_where, "applicable_presets", (request.preset_id,)
                )

        raw = _query_collection(
            collection,
            query_embedding,
            n_results=candidate_count,
            where=filtered_where,
        )
        _check_query_timeout(started_at, self.query_timeout_seconds)
        results = _normalize_chroma_results(raw, request.limit)
        return KnowledgeSearchResponse(
            results=tuple(results),
            search_mode="vector",
            index_metadata=dict(index_metadata),
        )

    def _where_for_request(self, request: KnowledgeQuery) -> Mapping[str, Any]:
        if self.index_kind == STANDARDS_KNOWLEDGE_SOURCE:
            return _standards_where(request)
        where = _profile_where(request)
        if request.document_ids:
            where = _add_where_any(where, "document_id", request.document_ids)
        if request.information_types:
            return _add_where_contains(
                where, "information_type", request.information_types
            )
        return where

    def _ensure_ready(self) -> tuple[Any, Any, Mapping[str, Any]]:
        if self._collection is not None and self._model is not None and self._index_metadata is not None:
            return self._collection, self._model, self._index_metadata

        with self._load_lock:
            if self._collection is not None and self._model is not None and self._index_metadata is not None:
                return self._collection, self._model, self._index_metadata

            index_manifest_path = self.index_dir / "index_manifest.json"
            if not index_manifest_path.is_file():
                raise KnowledgeUnavailableError(
                    f"RAG index is not built: {index_manifest_path}"
                )
            try:
                index_metadata = json.loads(index_manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise KnowledgeUnavailableError("RAG index manifest is invalid") from exc
            if not isinstance(index_metadata, Mapping):
                raise KnowledgeUnavailableError("RAG index manifest must be an object")

            source_manifest = load_knowledge_manifest(self.knowledge_manifest_path)
            source_revision = _manifest_revision(source_manifest)
            if str(index_metadata.get("knowledge_version", "")) != str(
                source_manifest.get("version", "")
            ) or str(index_metadata.get("code_revision", "")) != source_revision:
                raise KnowledgeUnavailableError(
                    "RAG index is stale; rebuild it from the current knowledge manifest."
                )
            if str(index_metadata.get("embedding_model", "")) != self.embedding_model:
                raise KnowledgeUnavailableError(
                    "RAG index embedding model does not match Backend configuration."
                )
            if str(index_metadata.get("collection_name", "")) != self.collection_name:
                raise KnowledgeUnavailableError(
                    "RAG index collection does not match Backend configuration."
                )
            try:
                embedding_dimension = int(index_metadata.get("embedding_dimension", 0))
            except (TypeError, ValueError) as exc:
                raise KnowledgeUnavailableError("RAG index embedding dimension is invalid") from exc
            if embedding_dimension != DEFAULT_EMBEDDING_DIMENSION:
                raise KnowledgeUnavailableError("RAG index embedding dimension is invalid")

            try:
                import chromadb  # type: ignore[import-not-found]
                from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
            except ImportError as exc:
                raise KnowledgeUnavailableError(
                    "RAG runtime requires chromadb and sentence-transformers."
                ) from exc
            try:
                client = chromadb.PersistentClient(path=str(self.index_dir))
                collection = client.get_collection(
                    name=self.collection_name,
                    embedding_function=None,
                )
                model_source = (
                    str(self.embedding_model_path)
                    if self.embedding_model_path is not None
                    else self.embedding_model
                )
                model = SentenceTransformer(
                    model_source,
                    device=resolve_embedding_device(self.device),
                )
            except KnowledgeError:
                raise
            except Exception as exc:
                raise KnowledgeUnavailableError(
                    "RAG index or embedding model could not be loaded."
                ) from exc

            self._collection = collection
            self._model = model
            self._index_metadata = dict(index_metadata)
            return collection, model, self._index_metadata


class CompositeKnowledgeRetriever:
    """Merge the project traffic index with the optional standards index.

    The two indexes intentionally keep separate source contracts.  The
    project index uses ``control``/``general`` eligibility flags, while the
    standards index uses ``standards``/``policy`` profiles and published
    status values.  This adapter gives Copilot one retriever without mixing
    those metadata rules or silently falling back when an explicitly
    requested source is unavailable.
    """

    def __init__(
        self,
        traffic_retriever: KnowledgeRetriever,
        standards_retriever: KnowledgeRetriever | None = None,
    ) -> None:
        self.traffic_retriever = traffic_retriever
        self.standards_retriever = standards_retriever

    def search(self, request: KnowledgeQuery) -> KnowledgeSearchResponse:
        if not isinstance(request, KnowledgeQuery):
            raise KnowledgeUnavailableError("Knowledge query must be a KnowledgeQuery")

        requested_sources = set(request.knowledge_sources)
        if requested_sources:
            use_traffic = TRAFFIC_KNOWLEDGE_SOURCE in requested_sources
            use_standards = bool(
                requested_sources
                & {STANDARDS_KNOWLEDGE_SOURCE, POLICY_KNOWLEDGE_SOURCE}
            )
        else:
            use_traffic = True
            # An unspecified source means ordinary project knowledge.  The
            # standards index is included only for an explicit standards
            # request or a backend-routed project-vs-standard comparison.
            use_standards = False

        responses: list[tuple[str, KnowledgeSearchResponse]] = []
        if use_traffic:
            # Each concrete retriever validates that the request only names
            # sources it owns.  A comparison request names both sources at
            # the composite boundary, so scope it before delegating.
            response = self.traffic_retriever.search(
                replace(
                    request,
                    knowledge_sources=(TRAFFIC_KNOWLEDGE_SOURCE,),
                )
            )
            responses.append(
                (TRAFFIC_KNOWLEDGE_SOURCE, _mark_knowledge_source(response, TRAFFIC_KNOWLEDGE_SOURCE))
            )

        if use_standards:
            if self.standards_retriever is None:
                raise KnowledgeUnavailableError(
                    "Standards/policy knowledge index is not configured."
                )
            standards_sources = tuple(
                source
                for source in request.knowledge_sources
                if source in {STANDARDS_KNOWLEDGE_SOURCE, POLICY_KNOWLEDGE_SOURCE}
            ) or (STANDARDS_KNOWLEDGE_SOURCE,)
            response = self.standards_retriever.search(
                replace(request, knowledge_sources=standards_sources)
            )
            responses.append(
                (
                    STANDARDS_KNOWLEDGE_SOURCE,
                    _mark_knowledge_source(response, STANDARDS_KNOWLEDGE_SOURCE),
                )
            )

        if not responses:
            raise KnowledgeUnavailableError(
                "No configured knowledge source matches the request."
            )

        def result_sort_key(item: KnowledgeResult) -> tuple[bool, float, str]:
            return (
                item.distance is None,
                float(item.distance) if item.distance is not None else float("inf"),
                item.chunk_id,
            )

        ranked_by_source = [
            (source, sorted(response.results, key=result_sort_key))
            for source, response in responses
        ]
        ranked: list[KnowledgeResult] = []
        seen_ids: set[str] = set()
        for _, source_results in ranked_by_source:
            for result in source_results:
                if result.chunk_id in seen_ids:
                    continue
                seen_ids.add(result.chunk_id)
                ranked.append(result)
        ranked.sort(key=result_sort_key)

        # An explicit comparison must give the model evidence from every
        # requested source. A pure global top-k merge can otherwise discard
        # the less-similar project result when the standards index has many
        # close matches, making the model compare standards against nothing.
        # Reserve the best result from each non-empty source, then fill the
        # remaining slots by global similarity.
        if len(ranked_by_source) > 1 and request.limit > 1:
            covered: list[KnowledgeResult] = []
            covered_ids: set[str] = set()
            for _, source_results in ranked_by_source:
                if not source_results:
                    continue
                result = next(
                    (item for item in source_results if item.chunk_id not in covered_ids),
                    None,
                )
                if result is not None:
                    covered.append(result)
                    covered_ids.add(result.chunk_id)
            for result in ranked:
                if len(covered) >= request.limit:
                    break
                if result.chunk_id not in covered_ids:
                    covered.append(result)
                    covered_ids.add(result.chunk_id)
            # For the backend-routed project-metric comparison, the current
            # project definition is the primary answer and standards are its
            # separate supporting evidence. Keep that order visible to the
            # model; otherwise several highly similar standard chunks can
            # precede the one canonical project chunk.
            if (
                CANONICAL_EFFICIENCY_DOCUMENT_ID in request.document_ids
                and use_traffic
                and use_standards
            ):
                source_order = {
                    TRAFFIC_KNOWLEDGE_SOURCE: 0,
                    STANDARDS_KNOWLEDGE_SOURCE: 1,
                }

                def comparison_sort_key(item: KnowledgeResult) -> tuple[Any, ...]:
                    source = str(item.metadata.get("knowledge_source", "")).lower()
                    return (source_order.get(source, 2), *result_sort_key(item))

                merged = sorted(covered, key=comparison_sort_key)
            else:
                merged = sorted(covered, key=result_sort_key)
        else:
            merged = ranked

        index_metadata = {
            "knowledge_sources": [source for source, _ in responses],
            "indexes": {
                source: dict(response.index_metadata)
                for source, response in responses
            },
        }
        return KnowledgeSearchResponse(
            results=tuple(merged[: request.limit]),
            search_mode=("vector_multi" if len(responses) > 1 else responses[0][1].search_mode),
            index_metadata=index_metadata,
        )


def resolve_embedding_device(device: str) -> str:
    """Normalize ``auto`` without importing torch until model use."""

    normalized = str(device).strip().lower() or "auto"
    if normalized != "auto":
        if normalized not in {"cpu", "cuda"}:
            raise KnowledgeBuildError("embedding device must be auto, cpu, or cuda")
        return normalized
    try:
        import torch  # type: ignore[import-not-found]

        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def _check_query_timeout(started_at: float, timeout_seconds: float) -> None:
    if time.monotonic() - started_at > timeout_seconds:
        raise KnowledgeUnavailableError(
            f"RAG query exceeded the configured timeout of {timeout_seconds:g} seconds"
        )


def _encode_documents(model: Any, texts: Sequence[str], *, batch_size: int) -> list[list[float]]:
    values = model.encode(
        list(texts),
        batch_size=batch_size,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return _embedding_rows(values)


def _encode_query(model: Any, text: str) -> list[float]:
    values = model.encode(
        [text],
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    rows = _embedding_rows(values)
    if len(rows) != 1:
        raise KnowledgeUnavailableError("Embedding model returned an invalid query vector")
    return rows[0]


def _embedding_rows(values: Any) -> list[list[float]]:
    if hasattr(values, "tolist"):
        values = values.tolist()
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise KnowledgeBuildError("Embedding model returned an invalid value")
    rows: list[list[float]] = []
    for row in values:
        if hasattr(row, "tolist"):
            row = row.tolist()
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)):
            raise KnowledgeBuildError("Embedding model returned an invalid row")
        rows.append([float(item) for item in row])
    return rows


def _embedding_dimension(embeddings: Sequence[Sequence[float]]) -> int:
    if not embeddings or not embeddings[0]:
        raise KnowledgeBuildError("Embedding model returned no vectors")
    dimension = len(embeddings[0])
    if any(len(row) != dimension for row in embeddings):
        raise KnowledgeBuildError("Embedding vectors have inconsistent dimensions")
    return dimension


def _stale_collection_ids(collection: Any, current_ids: Sequence[str]) -> list[str]:
    try:
        # Chroma always returns ids.  Request only metadata so rebuilding does
        # not pull every document body into memory just to remove stale rows.
        existing = collection.get(include=["metadatas"])
    except Exception:
        return []
    existing_ids = existing.get("ids", []) if isinstance(existing, Mapping) else []
    current_id_set = {str(item) for item in current_ids}
    return [str(item) for item in existing_ids if str(item) not in current_id_set]


def _write_json_last(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _profile_where(request: KnowledgeQuery) -> dict[str, Any]:
    conditions: list[dict[str, Any]] = [
        {"status": "current"},
        {"profile_general" if request.profile == GENERAL_PROFILE else "profile_control": True},
    ]
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


def _manifest_revision(manifest: Mapping[str, Any]) -> str:
    """Return the revision field used by either supported manifest format."""

    return str(
        manifest.get("project_revision")
        or manifest.get("code_revision")
        or ""
    )


def _standards_where(request: KnowledgeQuery) -> dict[str, Any]:
    requested = set(request.knowledge_sources) & {
        STANDARDS_KNOWLEDGE_SOURCE,
        POLICY_KNOWLEDGE_SOURCE,
    }
    if not requested:
        requested = {STANDARDS_KNOWLEDGE_SOURCE, POLICY_KNOWLEDGE_SOURCE}
    if request.profile == CONTROL_PROFILE:
        requested.discard(POLICY_KNOWLEDGE_SOURCE)
    if not requested:
        return {"profile": "__no_matching_knowledge_source__"}

    conditions: list[dict[str, Any]] = [
        _where_any("profile", sorted(requested)),
        _where_any(
            "status",
            ["published", "draft_for_approval", "planning_reference"],
        ),
    ]
    if request.information_types:
        conditions.append(_where_any("information_type", request.information_types))
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


def _where_any(field: str, values: Sequence[str]) -> dict[str, Any]:
    normalized = [str(value) for value in values if str(value)]
    if len(normalized) == 1:
        return {field: normalized[0]}
    return {"$or": [{field: value} for value in normalized]}


def _mark_knowledge_source(
    response: KnowledgeSearchResponse,
    source: str,
) -> KnowledgeSearchResponse:
    results: list[KnowledgeResult] = []
    for result in response.results:
        metadata = dict(result.metadata)
        if source == STANDARDS_KNOWLEDGE_SOURCE:
            profile = str(metadata.get("profile", "")).strip().lower()
            metadata["knowledge_source"] = (
                profile
                if profile in {STANDARDS_KNOWLEDGE_SOURCE, POLICY_KNOWLEDGE_SOURCE}
                else source
            )
        else:
            metadata["knowledge_source"] = source
        results.append(
            KnowledgeResult(
                chunk_id=result.chunk_id,
                text=result.text,
                metadata=metadata,
                distance=result.distance,
            )
        )
    return KnowledgeSearchResponse(
        results=tuple(results),
        search_mode=response.search_mode,
        index_metadata=response.index_metadata,
    )


def _add_where_contains(
    where: Mapping[str, Any], field: str, values: Sequence[str]
) -> dict[str, Any]:
    conditions = _where_conditions(where)
    if len(values) == 1:
        conditions.append({field: {"$contains": values[0]}})
    else:
        conditions.append(
            {"$or": [{field: {"$contains": value}} for value in values]}
        )
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


def _add_where_any(
    where: Mapping[str, Any], field: str, values: Sequence[str]
) -> dict[str, Any]:
    conditions = _where_conditions(where)
    conditions.append(_where_any(field, values))
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


def _where_conditions(where: Mapping[str, Any]) -> list[dict[str, Any]]:
    if set(where) == {"$and"} and isinstance(where.get("$and"), list):
        return [dict(item) for item in where["$and"] if isinstance(item, Mapping)]
    return [dict(where)]


def _query_collection(
    collection: Any,
    query_embedding: Sequence[float],
    *,
    n_results: int,
    where: Mapping[str, Any],
) -> Mapping[str, Any]:
    try:
        return collection.query(
            query_embeddings=[list(query_embedding)],
            n_results=n_results,
            where=dict(where),
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:
        raise KnowledgeUnavailableError("Chroma knowledge query failed") from exc


def _normalize_chroma_results(
    payload: Mapping[str, Any], limit: int
) -> list[KnowledgeResult]:
    ids = _first_nested(payload.get("ids"))
    documents = _first_nested(payload.get("documents"))
    metadatas = _first_nested(payload.get("metadatas"))
    distances = _first_nested(payload.get("distances"))
    results: list[KnowledgeResult] = []
    for index, chunk_id in enumerate(ids[:limit]):
        metadata = (
            metadatas[index]
            if index < len(metadatas) and isinstance(metadatas[index], Mapping)
            else {}
        )
        text = (
            str(documents[index])
            if index < len(documents) and documents[index] is not None
            else ""
        )
        distance = (
            float(distances[index])
            if index < len(distances) and distances[index] is not None
            else None
        )
        results.append(
            KnowledgeResult(
                chunk_id=str(chunk_id),
                text=text,
                metadata=dict(metadata),
                distance=distance,
            )
        )
    return results


def _first_nested(value: Any) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    if not value:
        return []
    first = value[0]
    if isinstance(first, Sequence) and not isinstance(first, (str, bytes)):
        return list(first)
    return list(value)


def _similarity_from_distance(distance: float | None) -> float | None:
    if distance is None:
        return None
    return round(1.0 - float(distance), 6)


def _safe_relative_path(value: Any, root: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeBuildError("Every manifest document needs a relative path")
    relative = Path(value.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise KnowledgeBuildError(f"Manifest path escapes knowledge root: {value}")
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise KnowledgeBuildError(f"Manifest path escapes knowledge root: {value}") from exc
    return relative


def _is_excluded_source(path: Path) -> bool:
    normalized_parts = {part.lower() for part in path.parts}
    if normalized_parts & _CONTROL_EXCLUDED_PATH_PARTS:
        return True
    name = path.name.upper()
    if name.startswith("KNOWLEDGE_"):
        return True
    if path.name in _EXCLUDED_FILE_NAMES:
        return True
    return path.suffix.lower() != ".md"


def _merge_document_metadata(
    document: Mapping[str, Any],
    frontmatter: Mapping[str, Any],
    *,
    manifest_version: str,
    manifest_revision: str,
) -> dict[str, Any]:
    merged = dict(document)
    for key, frontmatter_value in frontmatter.items():
        if key in document and _metadata_value(document[key]) != _metadata_value(frontmatter_value):
            raise KnowledgeBuildError(
                f"Manifest/frontmatter conflict for {document.get('path', '')}: {key}"
            )
        if key not in document:
            merged[key] = frontmatter_value
    merged.setdefault("status", "current")
    merged.setdefault("code_revision", manifest_revision)
    merged.setdefault("version", manifest_version)
    return merged


def _metadata_value(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return str(value).strip() if isinstance(value, str) else value


def _information_type_for_fragment(
    document: Mapping[str, Any], label_type: str | None
) -> str:
    if label_type:
        return label_type
    raw_type = str(document.get("information_type", "mixed")).strip() or "mixed"
    if raw_type != "mixed":
        return raw_type
    category = str(document.get("category", "")).strip()
    return _DEFAULT_MIXED_INFORMATION_TYPE.get(category, "mixed")


def _retrieval_profiles(
    document: Mapping[str, Any],
    *,
    relative_path: Path,
    information_type: str,
) -> list[str]:
    if str(document.get("status", "current")).strip().lower() != "current":
        return []
    profiles = [GENERAL_PROFILE]
    category = str(document.get("category", "")).strip()
    document_type = str(document.get("information_type", "")).strip()
    document_role = str(document.get("document_role", "")).strip().lower()
    if document_role == "evaluation_protocol":
        # Evaluation instructions are useful for general questions, but they
        # must not compete with current traffic-control knowledge in control.
        return [GENERAL_PROFILE]
    is_planning_document = (
        relative_path.parts[:1] == ("07_project",)
        and (
            relative_path.name.startswith("ai_")
            or relative_path.name in {"llm_decision_boundaries.md", "rag_retrieval_policy.md"}
        )
    ) or relative_path.as_posix() == "04_scenarios/ai_control_cases.md"
    if (
        category not in _CONTROL_EXCLUDED_CATEGORIES
        and document_type != "planning"
        and information_type not in {"planning", "mixed"}
        and not is_planning_document
        and not _is_excluded_source(relative_path)
    ):
        profiles.append(CONTROL_PROFILE)
    return profiles


def _chunk_metadata(
    document: Mapping[str, Any],
    *,
    relative_path: Path,
    title: str,
    section: str,
    information_type: str,
    document_information_type: str,
    profiles: Sequence[str],
    manifest_version: str,
    manifest_revision: str,
) -> dict[str, Any]:
    return {
        "document_id": str(document.get("id", relative_path.stem)),
        "source_path": relative_path.as_posix(),
        "title": title,
        "section": section,
        "information_type": information_type,
        "document_information_type": document_information_type,
        "document_role": str(document.get("document_role", "")).strip(),
        "status": str(document.get("status", "current")).strip().lower(),
        "priority": str(document.get("priority", "normal")),
        "applicable_events": _string_values(document.get("applicable_events", ())),
        "applicable_presets": _string_values(document.get("applicable_presets", ())),
        "sources": _string_values(document.get("sources", ())),
        # The top-level manifest revision is the source snapshot used for the
        # index.  Per-document revisions can lag behind after a knowledge
        # update and must not make a freshly rebuilt index look stale.
        "code_revision": manifest_revision,
        "knowledge_version": manifest_version,
        "retrieval_profiles": list(profiles),
        "profile_control": CONTROL_PROFILE in profiles,
        "profile_general": GENERAL_PROFILE in profiles,
    }


def _chroma_metadata(chunk: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "document_id",
        "source_path",
        "title",
        "section",
        "information_type",
        "document_information_type",
        "document_role",
        "status",
        "priority",
        "applicable_events",
        "applicable_presets",
        "sources",
        "code_revision",
        "knowledge_version",
        "retrieval_profiles",
        "profile_control",
        "profile_general",
    }
    metadata: dict[str, Any] = {}
    for key in allowed:
        if key not in chunk:
            continue
        value = chunk[key]
        # Empty array metadata is not accepted consistently by older Chroma
        # clients.  An omitted tag field has the same filtering semantics for
        # a non-empty requested event/preset, while keeping the JSONL contract
        # complete for review and rebuilds.
        if isinstance(value, list) and not value:
            continue
        metadata[key] = value
    return metadata


def _markdown_sections(markdown: str, fallback_title: str) -> list[tuple[str, str]]:
    lines = markdown.splitlines()
    sections: list[tuple[str, str]] = []
    heading_path: list[str] = []
    body: list[str] = []

    def flush() -> None:
        text = _normalize_text("\n".join(body))
        if text:
            section = " / ".join(heading_path) if heading_path else fallback_title
            sections.append((section, text))
        body.clear()

    for line in lines:
        match = _HEADING_PATTERN.match(line)
        if not match:
            body.append(line)
            continue
        level = len(match.group("marks"))
        heading = match.group("title").strip()
        if level == 1:
            # H1 is the document title, not a retrieval section.  Flush any
            # preceding body so a file without H2/H3 headings is still
            # represented rather than silently dropped.
            flush()
            heading_path = []
            continue
        if level > 3:
            body.append(line)
            continue
        flush()
        if level == 2:
            heading_path = [heading]
        elif heading_path:
            heading_path = [heading_path[0], heading]
        else:
            heading_path = [heading]
    flush()
    return sections


def _split_labeled_fragments(text: str) -> list[tuple[str | None, str]]:
    matches = list(_LABEL_PATTERN.finditer(text))
    if not matches:
        return [(None, text)]
    fragments: list[tuple[str | None, str]] = []
    prefix = text[: matches[0].start()]
    if prefix.strip():
        fragments.append((None, prefix))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        label = _LABEL_TO_INFORMATION_TYPE[match.group("label")]
        fragments.append((label, text[match.start() : end]))
    return fragments


def _split_large_text(text: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    paragraphs = [item for item in re.split(r"\n{2,}", text) if item.strip()]
    expanded_paragraphs: list[str] = []
    for paragraph in paragraphs:
        if _looks_like_markdown_table(paragraph):
            expanded_paragraphs.extend(
                _split_table_block(paragraph, max_chars=max_chars)
            )
        else:
            expanded_paragraphs.append(paragraph)
    parts: list[str] = []
    current = ""
    for paragraph in expanded_paragraphs:
        if _looks_like_markdown_table(paragraph):
            if current.strip():
                parts.append(current.strip())
                current = ""
            parts.append(paragraph.strip())
            continue
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            parts.append(current.strip())
            tail = current[-overlap_chars:] if overlap_chars else ""
            current = f"{tail}\n\n{paragraph}" if tail else paragraph
        else:
            current = paragraph
        while len(current) > max_chars:
            parts.append(current[:max_chars].strip())
            tail = current[max_chars - overlap_chars : max_chars] if overlap_chars else ""
            current = f"{tail}{current[max_chars:]}" if tail else current[max_chars:]
    if current.strip():
        parts.append(current.strip())
    return [item for item in parts if item]


def _looks_like_markdown_table(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2 or not lines[0].startswith("|"):
        return False
    separator = re.sub(r"[\s|:-]", "", lines[1])
    return bool(separator == "" and "-" in lines[1])


def _split_table_block(text: str, *, max_chars: int) -> list[str]:
    """Split a large Markdown table at complete rows and repeat its header."""

    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return [text]
    header = lines[:2]
    rows = lines[2:]
    header_text = "\n".join(header)
    if len(header_text) > max_chars:
        return _split_oversized_block(text, max_chars=max_chars)

    parts: list[str] = []
    current = list(header)
    for row in rows:
        candidate = "\n".join([*current, row])
        if len(candidate) <= max_chars:
            current.append(row)
            continue
        if len(current) > len(header):
            parts.append("\n".join(current).strip())
            current = list(header)
        row_with_header = "\n".join([*current, row])
        if len(row_with_header) <= max_chars:
            current.append(row)
        else:
            parts.append("\n".join(current).strip())
            parts.extend(_split_oversized_block(row, max_chars=max_chars))
            current = list(header)
    if len(current) > len(header):
        parts.append("\n".join(current).strip())
    elif not parts:
        parts.append(header_text)
    return parts


def _split_oversized_block(text: str, *, max_chars: int) -> list[str]:
    """Last-resort split for a single line/block that exceeds the limit."""

    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            newline = text.rfind("\n", start + max_chars // 2, end)
            if newline > start:
                end = newline
        part = text[start:end].strip()
        if part:
            parts.append(part)
        if end >= len(text):
            break
        start = end + (1 if text[end:end + 1] == "\n" else 0)
    return parts or [text[:max_chars]]


def _standalone_text(title: str, section: str, text: str) -> str:
    return _normalize_text(f"文档：{title}\n章节：{section}\n\n{text}")


def _chunk_id(
    document_id: str,
    section_index: int,
    section: str,
    fragment_index: int,
    part_index: int,
) -> str:
    slug = re.sub(r"[^\w-]+", "_", section, flags=re.UNICODE).strip("_")
    slug = slug[:80] or "section"
    return (
        f"{document_id}__s{section_index:03d}__{slug}"
        f"__f{fragment_index:02d}__p{part_index:02d}"
    )


def _normalize_text(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _extract_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if end is None:
        raise KnowledgeBuildError("Frontmatter starts with --- but has no closing ---")
    return _parse_simple_frontmatter(lines[1:end]), "\n".join(lines[end + 1 :])


def _parse_simple_frontmatter(lines: Sequence[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_key: str | None = None
    for line in lines:
        if not line.strip():
            continue
        list_match = re.match(r"^[ \t]+-[ \t]*(.*)$", line)
        if list_match and current_key:
            current = result.setdefault(current_key, [])
            if not isinstance(current, list):
                raise KnowledgeBuildError(f"Frontmatter key is both scalar and list: {current_key}")
            current.append(_parse_scalar(list_match.group(1)))
            continue
        match = _FRONTMATTER_KEY_PATTERN.match(line.strip())
        if not match:
            raise KnowledgeBuildError(f"Unsupported frontmatter line: {line}")
        current_key = match.group("key")
        raw_value = (match.group("value") or "").strip()
        result[current_key] = [] if not raw_value else _parse_scalar(raw_value)
    return result


def _parse_scalar(value: str) -> Any:
    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
        return cleaned[1:-1]
    if cleaned.startswith("[") and cleaned.endswith("]"):
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            return [item.strip() for item in cleaned[1:-1].split(",") if item.strip()]
        return parsed
    return cleaned


def _is_source_section(section: str) -> bool:
    tail = section.rsplit(" / ", 1)[-1].strip().lower()
    return tail in {"来源", "source", "sources"}


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, Sequence) and not isinstance(value, (bytes, str)):
        return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
    return []


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


__all__ = [
    "AI_EVALUATION_DOCUMENT_ID",
    "CANONICAL_EFFICIENCY_DOCUMENT_ID",
    "ChromaKnowledgeRetriever",
    "CompositeKnowledgeRetriever",
    "CONTROL_PROFILE",
    "DEFAULT_COLLECTION_NAME",
    "DEFAULT_EMBEDDING_DIMENSION",
    "DEFAULT_EMBEDDING_MODEL",
    "DEFAULT_QUERY_INSTRUCTION",
    "GENERAL_PROFILE",
    "POLICY_KNOWLEDGE_SOURCE",
    "STANDARDS_KNOWLEDGE_SOURCE",
    "SUPPORTED_KNOWLEDGE_SOURCES",
    "TRAFFIC_KNOWLEDGE_SOURCE",
    "KnowledgeBuildError",
    "KnowledgeError",
    "KnowledgeQuery",
    "KnowledgeResult",
    "KnowledgeRetriever",
    "KnowledgeRouting",
    "KnowledgeSearchResponse",
    "KnowledgeUnavailableError",
    "EMISSION_METRIC_DOCUMENT_ID",
    "SAFETY_METRIC_DOCUMENT_ID",
    "build_chroma_index",
    "build_knowledge_chunks",
    "load_knowledge_manifest",
    "route_knowledge_query",
    "resolve_embedding_device",
]
