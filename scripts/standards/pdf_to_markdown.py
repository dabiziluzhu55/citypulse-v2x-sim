#!/usr/bin/env python3
"""Convert a Chinese standards PDF into page-aware Markdown.

The converter is text-first.  It uses the PDF text layer when the extracted
text is usable and invokes Tesseract only for pages whose text layer is
missing or has broken CJK character mappings.  Rendered PNGs are temporary
OCR inputs and are not included in the Markdown output.

Example::

    python scripts/standards/pdf_to_markdown.py \
        --input "...\\国家标准：《城市交通运行状况评价规范》.pdf" \
        --output "...\\converted\\GB-T-33171-2016.md" \
        --title "城市交通运行状况评价规范" \
        --document-type standard \
        --profile standards \
        --authority "中华人民共和国国家标准" \
        --standard-number "GB/T 33171-2016" \
        --tesseract "C:\\Program Files\\PDF24\\tesseract\\tesseract.exe" \
        --tessdata-dir "C:\\Users\\25402\\AppData\\Local\\PDF24\\tesseract\\5.5.2\\tessdata"

The JSON quality report is written next to the Markdown file unless an
explicit ``--quality-report`` path is supplied.
"""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any

import pymupdf


CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
CJK_CHAR_CLASS = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
REPLACEMENT_CHAR = "\ufffd"


def _count_cjk(text: str) -> int:
    return len(CJK_RE.findall(text))


def _count_replacements(text: str) -> int:
    return text.count(REPLACEMENT_CHAR)


def _clean_text(text: str) -> str:
    """Normalize line endings without destroying page/layout evidence."""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ")
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    collapsed: list[str] = []
    previous_blank = False
    for line in lines:
        blank = not line.strip()
        if blank and previous_blank:
            continue
        collapsed.append(line)
        previous_blank = blank
    return "\n".join(collapsed).strip()


def _normalize_ocr_text(text: str) -> str:
    """Undo spacing that Tesseract commonly inserts between Chinese glyphs."""

    text = _clean_text(text)
    text = re.sub(
        rf"(?<=[{CJK_CHAR_CLASS}])\s+(?=[{CJK_CHAR_CLASS}])",
        "",
        text,
    )
    text = re.sub(
        rf"(?<=[{CJK_CHAR_CLASS}])\s+(?=[，。；：！？、）》】])",
        "",
        text,
    )
    text = re.sub(r"([（《【])\s+", r"\1", text)
    return text


def _text_layer_is_broken(text: str, *, expected_cjk: bool) -> bool:
    if not text.strip():
        return True
    if _count_replacements(text) > 0:
        return True
    if expected_cjk and _count_cjk(text) == 0:
        return True
    return False


def _run_tesseract(
    image_path: Path,
    *,
    tesseract: Path,
    tessdata_dir: Path,
    language: str,
    psm: int,
) -> tuple[str, str | None]:
    command = [
        str(tesseract),
        str(image_path),
        "stdout",
        "--tessdata-dir",
        str(tessdata_dir),
        "-l",
        language,
        "--psm",
        str(psm),
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stderr = completed.stderr.strip()
    if completed.returncode != 0:
        detail = stderr or f"tesseract exited with code {completed.returncode}"
        return "", detail
    return _normalize_ocr_text(completed.stdout), stderr or None


def _render_page(page: Any, output_path: Path, dpi: int) -> None:
    scale = dpi / 72.0
    pixmap = page.get_pixmap(
        matrix=pymupdf.Matrix(scale, scale),
        alpha=False,
    )
    pixmap.save(str(output_path))


def _yaml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _frontmatter(metadata: dict[str, str], page_count: int) -> str:
    lines = ["---"]
    for key, value in metadata.items():
        lines.append(f"{key}: {_yaml_quote(value)}")
    lines.append(f"page_count: {page_count}")
    lines.extend(["conversion_date: " + str(date.today()), "---", ""])
    return "\n".join(lines)


def _default_quality_path(output: Path) -> Path:
    return output.with_suffix(output.suffix + ".quality.json")


def _parse_page_numbers(value: str) -> set[int]:
    pages: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        page = int(item)
        if page < 1:
            raise ValueError("OCR page numbers must be positive.")
        pages.add(page)
    return pages


def convert_pdf(args: argparse.Namespace) -> tuple[Path, Path, dict[str, Any]]:
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    quality_path = (args.quality_report or _default_quality_path(output_path)).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    quality_path.parent.mkdir(parents=True, exist_ok=True)

    metadata = {
        "title": args.title,
        "document_type": args.document_type,
        "profile": args.profile,
        "authority": args.authority,
        "standard_number": args.standard_number,
        "source_filename": input_path.name,
        "source_path": str(input_path),
        "conversion_method": "pymupdf_text_layer_with_tesseract_fallback",
        "ocr_language": args.language,
    }

    quality: dict[str, Any] = {
        "source": str(input_path),
        "output": str(output_path),
        "page_count": 0,
        "ocr_pages": [],
        "text_layer_pages": [],
        "blank_pages": [],
        "pages_needing_review": [],
        "warnings": [],
        "pages": [],
    }
    requested_ocr_pages = _parse_page_numbers(args.ocr_pages)

    document = pymupdf.open(str(input_path))
    quality["page_count"] = document.page_count
    rendered_pages = 0
    markdown_parts = [
        _frontmatter(metadata, document.page_count),
        f"# {args.title}",
        "",
        "> 本文件由 PDF 文本抽取生成；`source-page` 标记对应原 PDF 页码。OCR 仅用于文本层异常页面。",
        "",
    ]

    with tempfile.TemporaryDirectory(prefix="pdf_to_markdown_") as temporary:
        temporary_path = Path(temporary)
        for page_number, page in enumerate(document, start=1):
            raw_text = _clean_text(page.get_text("text"))
            use_ocr = (
                args.force_ocr
                or page_number in requested_ocr_pages
                or _text_layer_is_broken(
                raw_text,
                expected_cjk=args.expected_cjk,
                )
            )
            ocr_error: str | None = None
            ocr_text = ""
            if use_ocr:
                image_path = temporary_path / f"page-{page_number:04d}.png"
                _render_page(page, image_path, args.dpi)
                rendered_pages += 1
                ocr_text, ocr_error = _run_tesseract(
                    image_path,
                    tesseract=args.tesseract,
                    tessdata_dir=args.tessdata_dir,
                    language=args.language,
                    psm=args.psm,
                )

            chosen_text = raw_text
            extraction_method = "text_layer"
            if ocr_text and (
                args.force_ocr
                or page_number in requested_ocr_pages
                or _count_replacements(raw_text) > 0
                or _count_cjk(ocr_text) >= _count_cjk(raw_text)
            ):
                chosen_text = ocr_text
                extraction_method = "tesseract_ocr"
            elif use_ocr and ocr_error:
                quality["warnings"].append(
                    f"page {page_number}: OCR failed: {ocr_error}"
                )

            if extraction_method == "tesseract_ocr":
                quality["ocr_pages"].append(page_number)
            elif not chosen_text:
                extraction_method = "blank_page"
                quality["blank_pages"].append(page_number)
            else:
                quality["text_layer_pages"].append(page_number)

            page_quality: dict[str, Any] = {
                "page": page_number,
                "method": extraction_method,
                "raw_text_chars": len(raw_text),
                "chosen_text_chars": len(chosen_text),
                "chosen_cjk_chars": _count_cjk(chosen_text),
                "raw_replacement_chars": _count_replacements(raw_text),
                "ocr_error": ocr_error,
            }
            likely_complex = any(marker in chosen_text for marker in ("表", "式", "附录"))
            if likely_complex:
                quality["pages_needing_review"].append(page_number)
                page_quality["needs_review"] = True
            else:
                page_quality["needs_review"] = False
            quality["pages"].append(page_quality)

            markdown_parts.extend(
                [
                    f"## 原文第 {page_number} 页",
                    f"<!-- source-page: {page_number} -->",
                    f"<!-- extraction: {extraction_method} -->",
                    chosen_text or "[本页未能抽取文本，需要人工复核]",
                    "",
                ]
            )

    document.close()
    quality["rendered_pages_for_ocr"] = rendered_pages
    if quality["pages_needing_review"]:
        quality["warnings"].append(
            "复杂表格、公式或附录页面已标记，需要抽样视觉复核；未将整份 PDF 转为上下文图片。"
        )
    output_path.write_text("\n".join(markdown_parts), encoding="utf-8", newline="\n")
    quality_path.write_text(
        json.dumps(quality, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return output_path, quality_path, quality


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--quality-report", type=Path, default=None)
    parser.add_argument("--title", required=True)
    parser.add_argument("--document-type", default="standard")
    parser.add_argument("--profile", default="standards")
    parser.add_argument("--authority", default="")
    parser.add_argument("--standard-number", default="")
    parser.add_argument(
        "--tesseract",
        type=Path,
        default=Path("tesseract"),
        help="Tesseract executable",
    )
    parser.add_argument(
        "--tessdata-dir",
        type=Path,
        required=True,
        help="Directory containing chi_sim.traineddata and other language data",
    )
    parser.add_argument("--language", default="chi_sim+eng")
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--psm", type=int, default=3)
    parser.add_argument(
        "--ocr-pages",
        default="",
        help="Comma-separated 1-based page numbers to OCR in addition to automatic detection",
    )
    parser.add_argument("--expected-cjk", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--force-ocr", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_path, quality_path, quality = convert_pdf(args)
    print(f"Generated Markdown: {output_path}")
    print(f"Generated quality report: {quality_path}")
    print(
        json.dumps(
            {
                "page_count": quality["page_count"],
                "ocr_pages": quality["ocr_pages"],
                "pages_needing_review": quality["pages_needing_review"],
                "warnings": quality["warnings"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
