#!/usr/bin/env python3
"""Build a manifest and reviewable RAG chunks from normalized MinerU Markdown.

This is the source-preparation stage for the standards/policy RAG profile. It
does not import embedding or Chroma dependencies and does not modify the raw
MinerU exports or the existing ``traffic_knowledge`` corpus.

The normalized Markdown is the embedding text source. The matching MinerU
JSON is used only for page/block provenance. Page numbers in the generated
chunks are one-based physical PDF pages; published standards also receive a
logical printed-page mapping inferred from the first normative page.

Example::

    python scripts/standards/build_standards_rag_sources.py

    python scripts/standards/build_standards_rag_sources.py \
        --normalized-dir "D:\\...\\国家与行业标准文件\\MinerU-normalized"
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from html import unescape
import json
import os
from pathlib import Path
import re
import unicodedata
from typing import Any, Iterable, Mapping, Sequence


DEFAULT_NORMALIZED_DIR = Path(
    r"D:\pycode\v2x-cloud\repos\国家与行业标准文件\MinerU-normalized"
)
KNOWLEDGE_VERSION = "standards-0.1"
CODE_REVISION = "standards-source-prep-2026-09-03"
DEFAULT_MAX_CHUNK_CHARS = 4_000
DEFAULT_OVERLAP_CHARS = 200


@dataclass(frozen=True)
class DocumentSpec:
    filename: str
    document_id: str
    category: str
    profile: str
    information_type: str
    priority: str


@dataclass(frozen=True)
class SourceDocument:
    spec: DocumentSpec
    path: Path
    frontmatter: Mapping[str, Any]
    source_pdf: Path
    source_json: Path
    page_count: int


@dataclass(frozen=True)
class Section:
    index: int
    path: tuple[str, ...]
    body: str

    @property
    def name(self) -> str:
        return " / ".join(self.path)

    @property
    def chapter(self) -> str:
        return self.path[0] if self.path else ""

    @property
    def clause(self) -> str:
        return self.path[-1] if self.path else ""


@dataclass(frozen=True)
class ContentBlock:
    kind: str
    text: str
    caption: str = ""
    table_row_start: int | None = None
    table_row_end: int | None = None


@dataclass(frozen=True)
class PageMap:
    page_texts: tuple[str, ...]
    page_search_texts: tuple[str, ...]
    body_start_page: int


DOCUMENT_SPECS = (
    DocumentSpec(
        filename="GB-T-36670-2018.md",
        document_id="standard_gbt36670_2018",
        category="standard",
        profile="standards",
        information_type="traffic_standard",
        priority="high",
    ),
    DocumentSpec(
        filename="GB-T-33171-2016.md",
        document_id="standard_gbt33171_2016",
        category="standard",
        profile="standards",
        information_type="traffic_standard",
        priority="high",
    ),
    DocumentSpec(
        filename="GB-T-34680-5-2022.md",
        document_id="standard_gbt34680_5_2022",
        category="standard",
        profile="standards",
        information_type="traffic_standard",
        priority="high",
    ),
    DocumentSpec(
        filename="GA-T-527-2-2024.md",
        document_id="industry_standard_gat527_2_2024",
        category="standard",
        profile="standards",
        information_type="traffic_standard",
        priority="high",
    ),
    DocumentSpec(
        filename="Xiongan-Planning-Outline.md",
        document_id="policy_xiongan_planning_outline_2018",
        category="planning",
        profile="policy",
        information_type="policy_reference",
        priority="high",
    ),
)

_FRONTMATTER_KEY = re.compile(
    r"^(?P<key>[A-Za-z_][A-Za-z0-9_-]*):(?:[ \t]*(?P<value>.*))?$"
)
_HEADING = re.compile(r"^(?P<marks>#{1,6})[ \t]+(?P<title>.+?)\s*$")
_TABLE = re.compile(r"<table\b.*?</table>", re.IGNORECASE | re.DOTALL)
_TABLE_ROW = re.compile(r"<tr\b.*?</tr>", re.IGNORECASE | re.DOTALL)
_TABLE_CELL = re.compile(
    r"<t[dh]\b(?P<attrs>[^>]*)>(?P<body>.*?)</t[dh]>",
    re.IGNORECASE | re.DOTALL,
)
_TABLE_ATTR = re.compile(r"(?P<name>rowspan|colspan)\s*=\s*[\"']?(?P<value>\d+)", re.IGNORECASE)
_HTML_TAG = re.compile(r"<[^>]+>")
_NUMBER_ONLY_HEADING = re.compile(
    r"^(?:\d+(?:\.\d+)*|[A-Z]\.[ \t]*\d+)$"
)
_CHAPTER_HEADING = re.compile(r"^第[^\s]+章(?:\s|$)")
_SUBSECTION_HEADING = re.compile(r"^第[^\s]+节(?:\s|$)")
_SECTION_NUMBER = re.compile(
    r"^(?P<number>(?:附\s*录\s*[A-Z](?:\.\d+)?|[A-Z]\.[ \t]*\d+|\d+(?:\.\d+)*))"
)
_CAPTION = re.compile(r"^(?:表|图)\s*[A-Za-z0-9一二三四五六七八九十百]+")


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return [item.strip() for item in value[1:-1].split(",") if item.strip()]
    return value


def _read_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = next(
        (index for index in range(1, len(lines)) if lines[index].strip() == "---"),
        None,
    )
    if end is None:
        raise ValueError("Frontmatter starts with --- but has no closing ---")
    result: dict[str, Any] = {}
    current_key: str | None = None
    for line in lines[1:end]:
        if not line.strip():
            continue
        list_match = re.match(r"^[ \t]+-[ \t]*(.*)$", line)
        if list_match and current_key:
            current = result.setdefault(current_key, [])
            if not isinstance(current, list):
                raise ValueError(f"Frontmatter key is both scalar and list: {current_key}")
            current.append(_parse_scalar(list_match.group(1)))
            continue
        match = _FRONTMATTER_KEY.match(line.strip())
        if not match:
            raise ValueError(f"Unsupported frontmatter line: {line}")
        current_key = match.group("key")
        raw_value = (match.group("value") or "").strip()
        result[current_key] = [] if not raw_value else _parse_scalar(raw_value)
    return result, "\n".join(lines[end + 1 :])


def _string(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _resolve_source_path(value: Any, normalized_dir: Path, *, label: str) -> Path:
    raw = _string(value)
    if not raw:
        raise ValueError(f"Missing {label} in normalized Markdown frontmatter")
    candidate = Path(raw).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    relative_candidate = (normalized_dir / candidate).resolve()
    if relative_candidate.is_file():
        return relative_candidate
    by_name = normalized_dir.parent / candidate.name
    if by_name.is_file():
        return by_name.resolve()
    raise FileNotFoundError(f"Cannot resolve {label}: {raw}")


def _relative_posix(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return Path(os.path.relpath(path.resolve(), root.resolve())).as_posix()


def _load_documents(normalized_dir: Path) -> list[SourceDocument]:
    documents: list[SourceDocument] = []
    for spec in DOCUMENT_SPECS:
        path = normalized_dir / spec.filename
        if not path.is_file():
            raise FileNotFoundError(f"Normalized Markdown is missing: {path}")
        frontmatter, _ = _read_frontmatter(path.read_text(encoding="utf-8"))
        required = (
            "title",
            "document_type",
            "profile",
            "authority",
            "document_number",
            "status",
            "source_filename",
            "source_pdf",
            "source_json",
            "page_count",
        )
        missing = [key for key in required if key not in frontmatter]
        if missing:
            raise ValueError(f"{path.name} is missing frontmatter: {missing}")
        if _string(frontmatter["profile"]) != spec.profile:
            raise ValueError(
                f"Profile mismatch for {path.name}: expected {spec.profile}, "
                f"got {frontmatter['profile']}"
            )
        if _string(frontmatter["status"]) == "draft_for_approval" and spec.profile != "standards":
            raise ValueError("Only a standards document may be draft_for_approval")
        source_pdf = _resolve_source_path(
            frontmatter["source_pdf"], normalized_dir, label="source_pdf"
        )
        source_json = _resolve_source_path(
            frontmatter["source_json"], normalized_dir, label="source_json"
        )
        page_count = int(_string(frontmatter["page_count"]))
        documents.append(
            SourceDocument(
                spec=spec,
                path=path.resolve(),
                frontmatter=frontmatter,
                source_pdf=source_pdf,
                source_json=source_json,
                page_count=page_count,
            )
        )
    return documents


def _node_text(node: Any) -> str:
    """Extract visible MinerU span text without using discarded blocks."""

    if isinstance(node, Mapping):
        spans = node.get("spans")
        if isinstance(spans, Sequence) and not isinstance(spans, (str, bytes)):
            return " ".join(
                _string(span.get("content") or span.get("html"))
                for span in spans
                if isinstance(span, Mapping)
                and _string(span.get("content") or span.get("html"))
            )
        content = node.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        for key in ("lines", "blocks", "para_blocks"):
            children = node.get(key)
            if isinstance(children, Sequence) and not isinstance(children, (str, bytes)):
                text = "\n".join(
                    child_text
                    for child in children
                    if (child_text := _node_text(child)).strip()
                )
                if text.strip():
                    return text
    elif isinstance(node, Sequence) and not isinstance(node, (str, bytes)):
        return "\n".join(
            child_text
            for child_text in (_node_text(item) for item in node)
            if child_text.strip()
        )
    return ""


def _searchable(text: str) -> str:
    normalized = unicodedata.normalize(
        "NFKC", _HTML_TAG.sub("", unescape(text))
    ).lower()
    return "".join(character for character in normalized if character.isalnum())


def _load_page_map(document: SourceDocument) -> PageMap:
    payload = json.loads(document.source_json.read_text(encoding="utf-8"))
    pages = payload.get("pdf_info")
    if not isinstance(pages, list):
        raise ValueError(f"MinerU JSON has no pdf_info[]: {document.source_json}")
    if len(pages) != document.page_count:
        raise ValueError(
            f"Page count mismatch for {document.path.name}: frontmatter={document.page_count}, "
            f"JSON={len(pages)}"
        )
    page_texts = tuple(
        _node_text(page.get("para_blocks", [])) if isinstance(page, Mapping) else ""
        for page in pages
    )
    page_search_texts = tuple(_searchable(text) for text in page_texts)
    if document.spec.profile == "standards":
        body_marker = _searchable("1 范围")
    else:
        body_marker = _searchable("第一章 总体要求")
    body_candidates = [
        index + 1
        for index, page_text in enumerate(page_search_texts)
        if body_marker and body_marker in page_text
    ]
    if not body_candidates:
        raise ValueError(
            f"Cannot locate normative/policy body marker for {document.path.name}"
        )
    # The same marker appears in the table of contents. The last occurrence
    # is the actual body start in these source PDFs.
    body_start_page = max(body_candidates)
    return PageMap(page_texts, page_search_texts, body_start_page)


def _normalize_heading_lines(lines: Sequence[str]) -> list[str]:
    """Merge MinerU's split numeric heading and heading-title pairs."""

    result: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        appendix_match = re.match(r"^附\s*录\s+([A-Z])\s*$", line.strip())
        if appendix_match:
            # A few MinerU pages lose the Markdown marker on an appendix
            # heading (for example the original Appendix B in GB/T 33171).
            # Promote only an unambiguous standalone appendix label.
            result.append(f"## 附录 {appendix_match.group(1)}")
            index += 1
            continue
        match = _HEADING.match(line.strip())
        if not match:
            result.append(line)
            index += 1
            continue
        title = match.group("title").strip()
        if _NUMBER_ONLY_HEADING.match(title):
            next_index = index + 1
            while next_index < len(lines) and not lines[next_index].strip():
                next_index += 1
            if next_index < len(lines):
                next_match = _HEADING.match(lines[next_index].strip())
                if next_match and len(next_match.group("marks")) == len(match.group("marks")):
                    result.append(
                        f"{match.group('marks')} {title} {next_match.group('title').strip()}"
                    )
                    index = next_index + 1
                    continue
        result.append(line)
        index += 1
    return result


def _build_sections(markdown: str) -> list[Section]:
    _, body = _read_frontmatter(markdown)
    lines = _normalize_heading_lines(body.splitlines())
    sections: list[Section] = []
    path: list[str] = []
    chapter: str | None = None
    current: list[str] = []

    def flush() -> None:
        if not path:
            current.clear()
            return
        text = _normalize_text("\n".join(current))
        if text:
            sections.append(
                Section(len(sections) + 1, tuple(path), text)
            )
        current.clear()

    for line in lines:
        match = _HEADING.match(line.strip())
        if not match:
            if path:
                current.append(line)
            continue
        level = len(match.group("marks"))
        title = match.group("title").strip()
        if level == 1:
            flush()
            if re.match(r"^附\s*录\s+[A-Z]", title):
                chapter = re.sub(r"\s+", " ", title)
                path = [chapter]
            else:
                path = []
                chapter = None
            continue
        if level > 3:
            if path:
                current.append(line)
            continue
        flush()
        if level == 2:
            if _CHAPTER_HEADING.match(title):
                chapter = title
                path = [title]
            elif re.match(r"^附\s*录\s+[A-Z]", title):
                chapter = re.sub(r"\s+", " ", title)
                path = [chapter]
            elif chapter and _SUBSECTION_HEADING.match(title):
                path = [chapter, title]
            elif chapter and chapter.startswith("附录"):
                path = [chapter, title]
            else:
                path = [title]
        else:
            if chapter and path and _SUBSECTION_HEADING.match(path[-1]):
                path = [chapter, path[-1], title]
            elif path:
                path = [path[0], title]
            else:
                path = [title]
    flush()
    return sections


def _normalize_text(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _plain_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "；", text, flags=re.IGNORECASE)
    text = re.sub(r"<eq>(.*?)</eq>", r"\1", text, flags=re.IGNORECASE | re.DOTALL)
    text = _HTML_TAG.sub("", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _split_long_text(text: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    text = _normalize_text(text)
    if len(text) <= max_chars:
        return [text] if text else []
    paragraphs = [item.strip() for item in re.split(r"\n{2,}", text) if item.strip()]
    parts: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                parts.append(current.strip())
                current = ""
            start = 0
            while start < len(paragraph):
                end = min(start + max_chars, len(paragraph))
                piece = paragraph[start:end].strip()
                if piece:
                    parts.append(piece)
                if end >= len(paragraph):
                    break
                start = max(start + 1, end - overlap_chars)
            continue
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        parts.append(current.strip())
        tail = current[-overlap_chars:] if overlap_chars else ""
        current = f"{tail}\n\n{paragraph}" if tail else paragraph
    if current.strip():
        parts.append(current.strip())
    return parts


def _caption_from_prefix(prefix: str) -> tuple[str, str]:
    lines = prefix.splitlines()
    last_nonempty = next(
        ((index, line.strip()) for index, line in reversed(list(enumerate(lines))) if line.strip()),
        None,
    )
    if last_nonempty and _CAPTION.match(last_nonempty[1]):
        index, caption = last_nonempty
        return "\n".join(lines[:index]), caption
    return prefix, ""


def _table_rows(table_html: str) -> list[list[str]]:
    rows: list[list[str]] = []
    active: dict[int, tuple[str, int]] = {}
    for raw_row in _TABLE_ROW.findall(table_html):
        row: list[str] = []
        column = 0
        for cell_match in _TABLE_CELL.finditer(raw_row):
            while column in active:
                value, remaining = active[column]
                row.append(value)
                if remaining <= 1:
                    del active[column]
                else:
                    active[column] = (value, remaining - 1)
                column += 1
            attrs = cell_match.group("attrs") or ""
            attributes = {
                match.group("name").lower(): int(match.group("value"))
                for match in _TABLE_ATTR.finditer(attrs)
            }
            colspan = max(1, attributes.get("colspan", 1))
            rowspan = max(1, attributes.get("rowspan", 1))
            value = _plain_html(cell_match.group("body"))
            for _ in range(colspan):
                row.append(value)
                if rowspan > 1:
                    active[column] = (value, rowspan - 1)
                column += 1
        while column in active:
            value, remaining = active[column]
            row.append(value)
            if remaining <= 1:
                del active[column]
            else:
                active[column] = (value, remaining - 1)
            column += 1
        if row:
            rows.append(row)
    return rows


def _table_first_row_has_span(table_html: str) -> bool:
    first_row = next(iter(_TABLE_ROW.findall(table_html)), "")
    for cell_match in _TABLE_CELL.finditer(first_row):
        attrs = cell_match.group("attrs") or ""
        if any(
            int(match.group("value")) > 1
            for match in _TABLE_ATTR.finditer(attrs)
        ):
            return True
    return False


def _render_table_row(headers: Sequence[str], row: Sequence[str], row_number: int) -> str:
    values: list[str] = []
    for index, value in enumerate(row):
        header = headers[index] if index < len(headers) and headers[index] else f"列{index + 1}"
        values.append(f"{header}：{value}" if value else f"{header}：")
    return f"第{row_number}行；" + "；".join(values)


def _table_content_blocks(
    table_html: str,
    caption: str,
    *,
    max_body_chars: int,
) -> list[ContentBlock]:
    rows = _table_rows(table_html)
    if not rows:
        return [ContentBlock("table", _plain_html(table_html), caption)]
    # Multi-level tables such as the control-target table use a grouped first
    # header row and a concrete second header row. Use the latter for labels
    # after expanding rowspan/colspan cells; this prevents metric values from
    # being assigned to the wrong column in the generated text.
    data_start = 2 if _table_first_row_has_span(table_html) and len(rows) > 1 else 1
    headers = rows[data_start - 1]
    header_text = "表头：" + " | ".join(headers)
    row_texts = [
        _render_table_row(headers, row, row_number)
        for row_number, row in enumerate(rows[data_start:], start=1)
    ]
    if not row_texts:
        row_texts = ["表格仅包含表头。"]
    pieces: list[ContentBlock] = []
    current = [header_text]
    current_start = 1
    current_end = 0
    for row_number, row_text in enumerate(row_texts, start=1):
        candidate = "\n".join([*current, row_text])
        if len(candidate) <= max_body_chars or len(current) == 1:
            current.append(row_text)
            current_end = row_number
            continue
        pieces.append(
            ContentBlock(
                "table",
                "\n".join(current),
                caption,
                current_start,
                current_end,
            )
        )
        current = [header_text, row_text]
        current_start = row_number
        current_end = row_number
    if len(current) > 1:
        pieces.append(
            ContentBlock(
                "table",
                "\n".join(current),
                caption,
                current_start,
                current_end,
            )
        )
    return pieces


def _content_blocks(
    section_body: str,
    *,
    max_body_chars: int,
    overlap_chars: int,
) -> list[ContentBlock]:
    blocks: list[ContentBlock] = []
    cursor = 0
    for match in _TABLE.finditer(section_body):
        prefix, caption = _caption_from_prefix(section_body[cursor : match.start()])
        for part in _split_long_text(
            prefix, max_chars=max_body_chars, overlap_chars=overlap_chars
        ):
            if part and not part.startswith("> 本文为 MinerU Markdown"):
                blocks.append(ContentBlock("text", part))
        blocks.extend(
            _table_content_blocks(
                match.group(0), caption, max_body_chars=max_body_chars
            )
        )
        cursor = match.end()
    suffix = section_body[cursor:]
    for part in _split_long_text(
        suffix, max_chars=max_body_chars, overlap_chars=overlap_chars
    ):
        if part and not part.startswith("> 本文为 MinerU Markdown"):
            blocks.append(ContentBlock("text", part))
    return blocks


def _section_number(section: Section) -> str:
    match = _SECTION_NUMBER.match(section.clause.strip())
    if not match:
        return ""
    number = re.sub(r"\s+", "", match.group("number"))
    return number.replace("附录", "附录")


def _slug(value: str) -> str:
    value = re.sub(r"[^\w.-]+", "_", value, flags=re.UNICODE).strip("_")
    return value[:96] or "section"


def _anchor_candidates(text: str) -> list[str]:
    plain = _plain_html(text)
    paragraphs = [item.strip() for item in re.split(r"\n{2,}", plain) if item.strip()]
    candidates: list[str] = []
    for paragraph in paragraphs:
        fragments: list[tuple[str, int]] = []
        # Table chunks use readable labels such as “指标名称：溢流率”. The
        # source JSON stores the same cell contents without our row prefix;
        # searching fragments makes table page mapping precise.
        for fragment in re.split(r"[；;|]", paragraph):
            fragment = fragment.strip()
            if not fragment:
                continue
            fragments.append((fragment, 8))
            if "：" in fragment:
                value = fragment.split("：", 1)[1].strip()
                if value:
                    fragments.append((value, 6))
        # Keep the whole paragraph as a lower-priority anchor. For tables it
        # contains our row labels and repeated headers, which are not present
        # in the MinerU JSON page text.
        fragments.append((paragraph, 20))
        for fragment, minimum_length in fragments:
            normalized = _searchable(fragment)
            if len(normalized) < minimum_length:
                continue
            windows = [normalized[:100], normalized[:60], normalized[-80:]]
            for window in windows:
                if len(window) >= 20 and window not in candidates:
                    candidates.append(window)
                elif len(window) >= minimum_length and window not in candidates:
                    candidates.append(window)
    return candidates[:30]


class PageLocator:
    def __init__(self, page_map: PageMap, profile: str) -> None:
        self.page_map = page_map
        self.profile = profile

    def _find_pages(self, needle: str, *, minimum_page: int) -> list[int]:
        normalized = _searchable(needle)
        if not normalized:
            return []
        return [
            index + 1
            for index, page_text in enumerate(self.page_map.page_search_texts)
            if index + 1 >= minimum_page and normalized in page_text
        ]

    def locate_sections(self, sections: Sequence[Section]) -> list[tuple[int | None, int | None, str]]:
        starts: list[tuple[int | None, str]] = []
        previous = self.page_map.body_start_page
        for section in sections:
            candidates: list[int] = []
            full = " ".join(section.path)
            # A parent chapter heading may occur on an earlier page than a
            # child clause (especially in formula-heavy appendices). Do not
            # use the parent as a fallback for every child, otherwise B.7,
            # B.8, ... would all inherit Appendix B's first page.
            variants = (full, section.clause)
            for variant in variants:
                candidates.extend(self._find_pages(variant, minimum_page=self.page_map.body_start_page))
            candidates = sorted(set(candidates))
            selected = next((page for page in candidates if page >= previous), None)
            if selected is None and candidates:
                selected = candidates[-1]
            starts.append(
                (
                    selected,
                    "section_heading" if selected is not None else "unresolved",
                )
            )
            if selected is not None:
                previous = selected
        locations: list[tuple[int | None, int | None, str]] = []
        for index, (start, method) in enumerate(starts):
            next_start = next(
                (item[0] for item in starts[index + 1 :] if item[0] is not None),
                len(self.page_map.page_texts),
            )
            end = max(start or next_start, next_start - 1) if start is not None else None
            locations.append((start, end, method))
        return locations

    def locate_block(
        self,
        block_text: str,
        *,
        section_start: int | None,
        section_end: int | None,
    ) -> tuple[list[int], str, float]:
        if section_start is None:
            return [], "unresolved", 0.0
        lower = max(self.page_map.body_start_page, section_start)
        # A table often starts immediately after a section heading and flows
        # onto the page where the next heading begins. Allow that one-page
        # boundary for text anchors; the section fallback remains the exact
        # heading page.
        upper = (section_end + 1) if section_end else len(self.page_map.page_texts)
        upper = max(lower, min(upper, len(self.page_map.page_texts)))
        matches: set[int] = set()
        for anchor in _anchor_candidates(block_text):
            for page in range(lower, upper + 1):
                if anchor in self.page_map.page_search_texts[page - 1]:
                    matches.add(page)
        if matches:
            return sorted(matches), "text_anchor", 0.95
        return [section_start], "section_heading", 0.75


def _document_manifest(
    document: SourceDocument,
    *,
    normalized_dir: Path,
) -> dict[str, Any]:
    fm = document.frontmatter
    status = _string(fm["status"])
    standard_number = _string(fm.get("document_number"))
    return {
        "id": document.spec.document_id,
        "path": document.path.name,
        "title": _string(fm["title"]),
        "category": document.spec.category,
        "document_type": _string(fm["document_type"]),
        "profile": document.spec.profile,
        "retrieval_profiles": [document.spec.profile],
        "information_type": document.spec.information_type,
        "authority": _string(fm["authority"]),
        "standard_number": standard_number,
        "document_number": standard_number,
        "status": status,
        "document_status": status,
        "source_filename": _string(fm["source_filename"]),
        "source_pdf": _relative_posix(document.source_pdf, normalized_dir),
        "source_json": _relative_posix(document.source_json, normalized_dir),
        "source_page_mapping": "MinerU JSON page_idx; physical PDF page is 1-based; original PDF is authoritative",
        "page_count": document.page_count,
        "page_number_basis": (
            "printed_page_inferred_from_normative_page_1"
            if document.spec.profile == "standards"
            else "physical_pdf_page_1_based"
        ),
        "priority": document.spec.priority,
        "sources": [_string(fm["source_filename"])],
        "applicable_events": [],
        "applicable_presets": [],
        "code_revision": CODE_REVISION,
        "normalized_date": _string(fm.get("normalized_date")),
        "metadata_source": "normalized Markdown frontmatter plus document profile policy",
    }


def _context_prefix(document: Mapping[str, Any], section: Section) -> str:
    number = _string(document.get("standard_number")) or "无"
    return (
        f"文档：{document['title']}\n"
        f"标准编号：{number}\n"
        f"章节：{section.name}\n"
        f"文档状态：{document['status']}\n\n"
    )


def _printed_pages(
    pages: Sequence[int], *, profile: str, body_start_page: int
) -> list[int] | None:
    if profile != "standards":
        return None
    return sorted({page - body_start_page + 1 for page in pages if page >= body_start_page})


def _build_chunks(
    document: SourceDocument,
    document_metadata: Mapping[str, Any],
    page_map: PageMap,
    *,
    max_chunk_chars: int,
    overlap_chars: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    markdown = document.path.read_text(encoding="utf-8")
    sections = _build_sections(markdown)
    locator = PageLocator(page_map, document.spec.profile)
    section_locations = locator.locate_sections(sections)
    context_length = len(_context_prefix(document_metadata, sections[0])) if sections else 100
    max_body_chars = max(200, max_chunk_chars - context_length - 20)
    chunks: list[dict[str, Any]] = []
    table_chunk_count = 0
    unresolved_count = 0
    for section, (section_start, section_end, section_method) in zip(
        sections, section_locations
    ):
        blocks = _content_blocks(
            section.body,
            max_body_chars=max_body_chars,
            overlap_chars=overlap_chars,
        )
        for block_index, block in enumerate(blocks, start=1):
            if not block.text.strip():
                continue
            prefix = _context_prefix(document_metadata, section)
            text = _normalize_text(
                prefix
                + (f"表格：{block.caption}\n" if block.caption else "")
                + block.text
            )
            # A very long caption or metadata prefix should not make the
            # reviewable chunk exceed the configured bound.
            parts = _split_long_text(
                text,
                max_chars=max_chunk_chars,
                overlap_chars=overlap_chars,
            )
            for part_index, part in enumerate(parts, start=1):
                # Use the content part rather than the metadata prefix for
                # provenance anchors, avoiding false matches on the title.
                pages, mapping_method, confidence = locator.locate_block(
                    block.text,
                    section_start=section_start,
                    section_end=section_end,
                )
                source_pages = pages
                printed_pages = _printed_pages(
                    source_pages,
                    profile=document.spec.profile,
                    body_start_page=page_map.body_start_page,
                )
                if mapping_method == "unresolved":
                    unresolved_count += 1
                slug = _slug(section.name)
                chunk_id = (
                    f"{document.spec.document_id}__s{section.index:03d}__"
                    f"{slug}__b{block_index:03d}__p{part_index:02d}"
                )
                chunk = {
                    "chunk_id": chunk_id,
                    "document_id": document.spec.document_id,
                    "source_path": document.path.name,
                    "source_pdf": document_metadata["source_pdf"],
                    "source_json": document_metadata["source_json"],
                    "title": document_metadata["title"],
                    "standard_number": document_metadata["standard_number"],
                    "document_number": document_metadata["document_number"],
                    "authority": document_metadata["authority"],
                    "status": document_metadata["status"],
                    "document_status": document_metadata["document_status"],
                    "profile": document_metadata["profile"],
                    "retrieval_profiles": document_metadata["retrieval_profiles"],
                    "information_type": document_metadata["information_type"],
                    "priority": document_metadata["priority"],
                    "sources": document_metadata["sources"],
                    "applicable_events": document_metadata["applicable_events"],
                    "applicable_presets": document_metadata["applicable_presets"],
                    "knowledge_version": KNOWLEDGE_VERSION,
                    "code_revision": CODE_REVISION,
                    "section": section.name,
                    "section_path": list(section.path),
                    "chapter": section.chapter,
                    "clause": section.clause,
                    "section_number": _section_number(section),
                    "chunk_type": block.kind,
                    "table_caption": block.caption,
                    "table_row_start": block.table_row_start,
                    "table_row_end": block.table_row_end,
                    "source_page": source_pages[0] if source_pages else None,
                    "source_pages": source_pages,
                    "printed_page": printed_pages[0] if printed_pages else None,
                    "printed_pages": printed_pages,
                    "source_page_basis": document_metadata["page_number_basis"],
                    "page_mapping_status": "mapped" if source_pages else "unresolved",
                    "page_mapping_method": mapping_method,
                    "page_mapping_confidence": confidence,
                    "section_mapping_method": section_method,
                    "text": part,
                }
                chunks.append(chunk)
                if block.kind == "table":
                    table_chunk_count += 1
    if not chunks:
        raise ValueError(f"No chunks generated for {document.path.name}")
    return chunks, {
        "document_id": document.spec.document_id,
        "source_path": document.path.name,
        "section_count": len(sections),
        "chunk_count": len(chunks),
        "table_chunk_count": table_chunk_count,
        "unresolved_page_mapping_count": unresolved_count,
        "body_start_pdf_page": page_map.body_start_page,
    }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_chunks(path: Path, chunks: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def build_sources(
    normalized_dir: Path,
    manifest_output: Path,
    chunks_output: Path,
    report_output: Path,
    *,
    max_chunk_chars: int,
    overlap_chars: int,
) -> dict[str, Any]:
    if max_chunk_chars < 500:
        raise ValueError("max_chunk_chars must be at least 500")
    if overlap_chars < 0 or overlap_chars >= max_chunk_chars:
        raise ValueError("overlap_chars must be non-negative and smaller than max_chunk_chars")
    normalized_dir = normalized_dir.resolve()
    documents = _load_documents(normalized_dir)
    manifest_documents = [
        _document_manifest(document, normalized_dir=normalized_dir)
        for document in documents
    ]
    manifest = {
        "version": KNOWLEDGE_VERSION,
        "code_revision": CODE_REVISION,
        "generated_at": date.today().isoformat(),
        "source_root": ".",
        "raw_source_root": "..",
        "embedding_source": "normalized MinerU Markdown",
        "page_metadata_source": "matching MinerU JSON pdf_info[].page_idx",
        "page_number_policy": "source_pages are one-based physical PDF pages; printed_pages are inferred only for standards",
        "documents": manifest_documents,
    }
    _write_json(manifest_output, manifest)

    all_chunks: list[dict[str, Any]] = []
    document_reports: list[dict[str, Any]] = []
    for document, metadata in zip(documents, manifest_documents):
        page_map = _load_page_map(document)
        chunks, report = _build_chunks(
            document,
            metadata,
            page_map,
            max_chunk_chars=max_chunk_chars,
            overlap_chars=overlap_chars,
        )
        all_chunks.extend(chunks)
        document_reports.append(report)
    ids = [chunk["chunk_id"] for chunk in all_chunks]
    if len(ids) != len(set(ids)):
        raise ValueError("Generated chunk IDs are not unique")
    _write_chunks(chunks_output, all_chunks)
    report = {
        "knowledge_version": KNOWLEDGE_VERSION,
        "generated_at": date.today().isoformat(),
        "normalized_dir": str(normalized_dir),
        "manifest_output": str(manifest_output.resolve()),
        "chunks_output": str(chunks_output.resolve()),
        "document_count": len(documents),
        "chunk_count": len(all_chunks),
        "table_chunk_count": sum(item["table_chunk_count"] for item in document_reports),
        "unresolved_page_mapping_count": sum(
            item["unresolved_page_mapping_count"] for item in document_reports
        ),
        "max_chunk_chars": max_chunk_chars,
        "overlap_chars": overlap_chars,
        "documents": document_reports,
        "validation": {
            "unique_chunk_ids": True,
            "all_documents_have_source_pdf": True,
            "all_documents_have_source_json": True,
            "page_count_checked_against_mineru_json": True,
            "embedding_index_built": False,
        },
    }
    _write_json(report_output, report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--normalized-dir",
        type=Path,
        default=DEFAULT_NORMALIZED_DIR,
        help="Directory containing the cleaned MinerU Markdown files",
    )
    parser.add_argument(
        "--manifest-output",
        type=Path,
        default=None,
        help="Manifest output; defaults to <normalized-dir>/rag_manifest.json",
    )
    parser.add_argument(
        "--chunks-output",
        type=Path,
        default=None,
        help="JSONL output; defaults to <normalized-dir>/rag_build/chunks.jsonl",
    )
    parser.add_argument(
        "--report-output",
        type=Path,
        default=None,
        help="Build report; defaults to <normalized-dir>/rag_build/build_report.json",
    )
    parser.add_argument(
        "--max-chunk-chars",
        type=int,
        default=DEFAULT_MAX_CHUNK_CHARS,
    )
    parser.add_argument(
        "--overlap-chars",
        type=int,
        default=DEFAULT_OVERLAP_CHARS,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    normalized_dir = args.normalized_dir.resolve()
    manifest_output = (
        args.manifest_output.resolve()
        if args.manifest_output
        else normalized_dir / "rag_manifest.json"
    )
    chunks_output = (
        args.chunks_output.resolve()
        if args.chunks_output
        else normalized_dir / "rag_build" / "chunks.jsonl"
    )
    report_output = (
        args.report_output.resolve()
        if args.report_output
        else normalized_dir / "rag_build" / "build_report.json"
    )
    report = build_sources(
        normalized_dir,
        manifest_output,
        chunks_output,
        report_output,
        max_chunk_chars=args.max_chunk_chars,
        overlap_chars=args.overlap_chars,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
