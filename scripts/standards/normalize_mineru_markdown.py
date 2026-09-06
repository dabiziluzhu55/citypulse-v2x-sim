#!/usr/bin/env python3
"""Create non-destructive, RAG-oriented views of MinerU Markdown.

The raw MinerU exports remain untouched.  This script removes administrative
front matter (cover, table of contents, preface and references), keeps the
normative body/appendices, applies only evidence-backed mechanical repairs,
and writes a per-document quality report.

The JSON export is kept as an auxiliary source of page/block metadata.  It is
not embedded as text.  The current Markdown export does not contain page
markers, so the report records the JSON page count for the later page-mapping
step.

Example::

    python scripts/standards/normalize_mineru_markdown.py \
        --input-dir "D:\\...\\国家与行业标准文件\\MinerU-converted" \
        --output-dir "D:\\...\\国家与行业标准文件\\MinerU-normalized"
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import date
from html import unescape
import json
from pathlib import Path
import re
from typing import Any, Callable


@dataclass(frozen=True)
class DocumentSpec:
    key: str
    filename_marker: str
    output_name: str
    title: str
    document_type: str
    profile: str
    authority: str
    document_number: str
    status: str
    source_filename: str
    body_start_pattern: str
    reference_pattern: str | None


SPECS = (
    DocumentSpec(
        key="gb_t_36670_2018",
        filename_marker="城市道路交通组织设计规范",
        output_name="GB-T-36670-2018.md",
        title="城市道路交通组织设计规范",
        document_type="standard",
        profile="standards",
        authority="中华人民共和国国家标准",
        document_number="GB/T 36670-2018",
        status="published",
        source_filename="国家标准：《城市道路交通组织设计规范》.pdf",
        body_start_pattern=r"^##\s+1\s+范围\s*$",
        reference_pattern=r"^##\s*参\s*考\s*文\s*献\s*$",
    ),
    DocumentSpec(
        key="gb_t_33171_2016",
        filename_marker="城市交通运行状况评价规范",
        output_name="GB-T-33171-2016.md",
        title="城市交通运行状况评价规范",
        document_type="standard",
        profile="standards",
        authority="中华人民共和国国家标准",
        document_number="GB/T 33171-2016",
        status="published",
        source_filename="国家标准：《城市交通运行状况评价规范》.pdf",
        body_start_pattern=r"^##\s+1\s+范围\s*$",
        reference_pattern=r"^##\s*参\s*考\s*文\s*献\s*$",
    ),
    DocumentSpec(
        key="gb_t_34680_5_2022",
        filename_marker="智慧城市评价模型及基础评价指标体系",
        output_name="GB-T-34680-5-2022.md",
        title="智慧城市评价模型及基础评价指标体系 第 5 部分：交通",
        document_type="standard",
        profile="standards",
        authority="中华人民共和国国家标准",
        document_number="GB/T 34680.5-2022",
        status="published",
        source_filename="国家标准：《智慧城市评价模型及基础评价指标体系 第5部分：交通》.pdf",
        body_start_pattern=r"^##\s+1\s+范围\s*$",
        reference_pattern=r"^##\s*参\s*考\s*文\s*献\s*$",
    ),
    DocumentSpec(
        key="xiongan_planning_outline",
        filename_marker="河北雄安新区规划纲要",
        output_name="Xiongan-Planning-Outline.md",
        title="河北雄安新区规划纲要",
        document_type="planning_outline",
        profile="policy",
        authority="中共河北省委、河北省人民政府",
        document_number="",
        status="planning_reference",
        source_filename="河北雄安新区规划纲要.pdf",
        body_start_pattern=r"^##\s+第一章\s+总体要求\s*$",
        reference_pattern=None,
    ),
    DocumentSpec(
        key="ga_t_527_2_2024",
        filename_marker="道路交通信号控制方式",
        output_name="GA-T-527-2-2024.md",
        title="道路交通信号控制方式 第 2 部分：通行状态与控制效益评估指标及方法",
        document_type="industry_standard",
        profile="standards",
        authority="中华人民共和国公安部",
        document_number="GA/T 527.2-2024",
        status="draft_for_approval",
        source_filename="行业标准：《道路交通信号控制方式 第2部分通行状态与控制效益评估指标及方法》（报批稿）.pdf",
        body_start_pattern=r"^##\s+1\s+范围\s*$",
        reference_pattern=r"^##\s*参\s*考\s*文\s*献\s*$",
    ),
)


# MinerU drops decimal points from some all-numeric headings in GB/T
# 33171.  These are unambiguous because the document's table of contents and
# surrounding section hierarchy provide the expected numbering.
GB_T_33171_HEADING_MAP = {
    "34": "3.4",
    "37": "3.7",
    "310": "3.10",
    "313": "3.13",
    "51": "5.1",
    "52": "5.2",
    "53": "5.3",
    "54": "5.4",
    "55": "5.5",
    "551": "5.5.1",
    "552": "5.5.2",
    "61": "6.1",
    "62": "6.2",
    "71": "7.1",
    "72": "7.2",
    "721": "7.2.1",
    "7211": "7.2.1.1",
    "7212": "7.2.1.2",
    "7213": "7.2.1.3",
    "7214": "7.2.1.4",
    "722": "7.2.2",
    "7221": "7.2.2.1",
    "7222": "7.2.2.2",
    "7223": "7.2.2.3",
    "7224": "7.2.2.4",
    "723": "7.2.3",
    "7231": "7.2.3.1",
    "7232": "7.2.3.2",
    "7233": "7.2.3.3",
    "81": "8.1",
    "82": "8.2",
}


GB_T_33171_SUBHEADING_MAP = {
    "611": "6.1.1",
    "612": "6.1.2",
    "613": "6.1.3",
    "711": "7.1.1",
    "712": "7.1.2",
    "713": "7.1.3",
    "714": "7.1.4",
}


INDUSTRY_FORMULA_TAGS = [
    *[f"B.{number}" for number in range(1, 22)],
    "B.22",
    "B.23",
    "B.24",
    "B.25",
    "B.26",
    "C.1",
]


def _find_spec(path: Path) -> DocumentSpec:
    matches = [spec for spec in SPECS if spec.filename_marker in path.name]
    if len(matches) != 1:
        raise ValueError(f"Cannot identify document spec for {path.name!r}")
    return matches[0]


def _find_json(input_dir: Path, spec: DocumentSpec) -> Path:
    candidates = [
        path
        for path in input_dir.glob("*.json")
        if spec.filename_marker in path.name
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"Expected one MinerU JSON for {spec.key}, found {len(candidates)}"
        )
    return candidates[0]


def _find_source_pdf(source_root: Path, spec: DocumentSpec) -> Path:
    path = source_root / spec.source_filename
    if path.exists():
        return path
    # Keep the process usable if the source filename uses a small punctuation
    # variation while retaining a deterministic match.
    candidates = [
        candidate
        for candidate in source_root.glob("*.pdf")
        if spec.filename_marker in candidate.name
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"Cannot find source PDF for {spec.key}; expected {path}"
        )
    return candidates[0]


def _json_summary(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    pages = data.get("pdf_info", [])
    block_types: Counter[str] = Counter()
    discarded = 0
    for page in pages:
        discarded += len(page.get("discarded_blocks", []))
        for block in page.get("para_blocks", []):
            block_types[str(block.get("type", "unknown"))] += 1
    return {
        "path": str(path.resolve()),
        "page_count": len(pages),
        "backend": data.get("_backend"),
        "mineru_version": data.get("_version_name"),
        "ocr_enabled": data.get("_ocr_enable"),
        "block_types": dict(sorted(block_types.items())),
        "discarded_block_count": discarded,
    }


def _split_body(lines: list[str], spec: DocumentSpec) -> tuple[list[str], int, int]:
    start_re = re.compile(spec.body_start_pattern)
    start_index = next(
        (index for index, line in enumerate(lines) if start_re.match(line.strip())),
        None,
    )
    if start_index is None:
        raise ValueError(f"Body start not found for {spec.key}")

    end_index = len(lines)
    if spec.reference_pattern:
        reference_re = re.compile(spec.reference_pattern)
        end_index = next(
            (
                index
                for index in range(start_index + 1, len(lines))
                if reference_re.match(lines[index].strip())
            ),
            len(lines),
        )
    return lines[start_index:end_index], start_index + 1, end_index


def _normalize_heading(line: str, spec: DocumentSpec) -> str:
    if spec.key != "gb_t_33171_2016":
        return line
    match = re.match(r"^(##+)\s+(\d+)(?:\s+(.*))?$", line.strip())
    if not match:
        return line
    number = GB_T_33171_HEADING_MAP.get(match.group(2))
    if not number:
        return line
    title = (match.group(3) or "").strip()
    return f"{match.group(1)} {number}{(' ' + title) if title else ''}".rstrip()


def _merge_gb_t_33171_term_headings(lines: list[str]) -> list[str]:
    """Join MinerU's split term-number and term-name headings."""

    output: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        match = re.match(r"^##\s+(3\.\d+)$", line.strip())
        if not match:
            output.append(line)
            index += 1
            continue

        next_index = index + 1
        while next_index < len(lines) and not lines[next_index].strip():
            next_index += 1
        if next_index >= len(lines):
            output.append(line)
            index += 1
            continue

        candidate = lines[next_index].strip()
        if candidate.startswith("## "):
            candidate = candidate[3:].strip()
        if (
            candidate
            and len(candidate) <= 100
            and re.search(r"[A-Za-z]", candidate)
            and not candidate.endswith(("。", ".", ";", "；"))
        ):
            output.append(f"## {match.group(1)} {candidate}")
            index = next_index + 1
            continue
        output.append(line)
        index += 1
    return output


def _promote_gb_t_33171_subheadings(lines: list[str]) -> list[str]:
    """Turn MinerU's inline numeric subheadings into real Markdown headings."""

    output: list[str] = []
    for line in lines:
        match = re.match(r"^(611|612|613|711|712|713|714)\s+(.+)$", line.strip())
        if not match:
            output.append(line)
            continue
        output.extend(
            [
                f"### {GB_T_33171_SUBHEADING_MAP[match.group(1)]}",
                "",
                match.group(2).strip(),
            ]
        )
    return output


def _normalize_eq_tags(line: str, repair_log: list[str]) -> str:
    def replace(match: re.Match[str]) -> str:
        repair_log.append("converted MinerU <eq> tag to inline LaTeX")
        return rf"\({unescape(match.group(1)).strip()}\)"

    return re.sub(r"<eq>(.*?)</eq>", replace, line)


def _replace_once(
    text: str,
    old: str,
    new: str,
    repair_log: list[str],
    label: str,
) -> str:
    count = text.count(old)
    if count:
        text = text.replace(old, new)
        repair_log.append(f"{label} (occurrences={count})")
    return text


def _repair_xiongan(text: str, repair_log: list[str]) -> str:
    # These values were checked against the corresponding original PDF pages
    # and the independent text-layer extraction retained in converted/.
    repairs = (
        (
            "规划期限至 年",
            "规划期限至2035年",
            "verified planning period from original PDF page 2",
        ),
        (
            "距北京 天津均为 公里 距石家庄 公里 距保定 公里",
            "距北京、天津均为105公里，距石家庄155公里，距保定30公里",
            "verified distance figures from original PDF page 2",
        ),
        (
            "与以 年北京冬奥会",
            "与以2022年北京冬奥会",
            "verified year from independent text-layer extraction",
        ),
        (
            "绿色交通出行比例达到",
            "绿色交通出行比例达到90％",
            "verified target from independent text-layer extraction",
        ),
        (
            "新建 千伏和 千伏变电站",
            "新建500千伏和220千伏变电站",
            "verified voltage figures from original PDF page 9",
        ),
        (
            "达到99� 999％",
            "达到99.999％",
            "verified reliability figure from original PDF page 9",
        ),
    )
    for old, new, label in repairs:
        text = _replace_once(text, old, new, repair_log, label)
    return text


def _repair_gb_t_33171(text: str, repair_log: list[str]) -> str:
    text = _repair_gb_t_33171_sections(text, repair_log)
    text = _replace_once(
        text,
        "综合性指标采用城市交通运行指数,可按照拥堵里程、行程时间、延误时间三种方法换算,取值范围为 。",
        "综合性指标采用城市交通运行指数，可按照拥堵里程、行程时间、延误时间三种方法换算，取值范围为0~10。",
        repair_log,
        "restored TPI range from the standard's table and independent text-layer extraction",
    )
    text = re.sub(
        r"\\tag\{……\((\d+)\}",
        lambda match: (repair_log.append(
            f"normalized equation tag to ({match.group(1)})"
        ) or rf"\tag{{{match.group(1)}}}"),
        text,
    )
    text = _replace_once(
        text,
        "/ — 颜色的表示方法",
        "GB/T 3977—2008 颜色的表示方法",
        repair_log,
        "restored normative reference GB/T 3977—2008",
    )
    text = _replace_once(
        text,
        "/ — 道路交通信息服务 交通状况描述",
        "GB/T 29107—2012 道路交通信息服务 交通状况描述",
        repair_log,
        "restored normative reference GB/T 29107—2012",
    )
    text = _replace_once(
        text,
        "表 按照 / — 规定的颜色表示方法",
        "表D1按照GB/T 3977—2008规定的颜色表示方法",
        repair_log,
        "restored Appendix D reference sentence",
    )
    text = re.sub(
        r"GB/T\s*(33171)(?=—|-|\s)",
        r"GB/T \1",
        text,
    )
    text = re.sub(
        r"(?<![A-Za-z])(/\s*[—-])",
        lambda match: match.group(1),
        text,
    )
    return text


def _replace_gb_section(
    text: str,
    pattern: str,
    replacement: str,
    repair_log: list[str],
    label: str,
) -> str:
    text, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    if count:
        repair_log.append(label)
    return text


def _repair_gb_t_33171_sections(text: str, repair_log: list[str]) -> str:
    """Restore unambiguous values lost by MinerU's font mapping.

    The replacements below are limited to clauses that were cross-checked
    against the original PDF pages and the independent PyMuPDF extraction.
    Formula bodies emitted by MinerU are retained; only their surrounding
    explanatory text is repaired.
    """

    text = _replace_gb_section(
        text,
        r"(## 4 评价内容和流程\n\n).*?(?=\n## 5 评价对象和范围划分)",
        r"\1城市道路交通运行状况评价是指针对评价对象和范围，使用评价数据和评价指标，对城市道路交通运行状况进行评价，得到城市道路交通运行状况等级等评价结果的过程。应遵循以下步骤：\n\na）按照第5章要求，选择评价对象和范围；\n\nb）按照第6章要求，采集评价所需的道路属性数据和交通流运行数据；\n\nc）按照第5章选择的评价对象，按照第7章选择指标，进行指标计算；\n\nd）按照第9章要求，汇集评价指标得出评价结果。",
        repair_log,
        "restored 4 evaluation workflow references and list markers",
    )
    text = _replace_gb_section(
        text,
        r"(## 5\.2 城市道路网的划定\n\n).*?(?=\n表 1 各等级道路最小覆盖比例表)",
        r"\1### 5.2.1\n\n城市道路网包括快速路、主干路、次干路、支路。各自含义如下：\n\na）快速路是指城市道路中设有中央分隔带，单向设置不少于两条车道，全部采用立体交叉与控制出入，实现交通连续通行的道路；\n\nb）主干路是指在城市道路网中连接城市各主要分区，以交通功能为主的道路；\n\nc）次干路是指城市道路网中的与主干路结合，以集散交通的功能为主、兼有服务功能的道路；\n\nd）支路是指城市道路网中与次干路和居住区、工业区、交通设施等内部道路相连接，解决局部地区服务功能的道路。\n\n### 5.2.2\n\n城市道路网应按照城市建成区划定，城市道路网交通运行状况评价宜包括道路网内所有路段。\n\n### 5.2.3\n\n若道路运行动态数据不具备覆盖道路网内所有路段的条件，应至少满足表1要求。\n\n",
        repair_log,
        "restored 5.2 subclause numbers and list markers",
    )
    text = _replace_gb_section(
        text,
        r"(## 5\.3 城市分区域道路网的划分\n\n).*?(?=\n## 5\.4 道路的划分)",
        r"\1城市分区域道路网宜按照以下方法划分：\n\na）按照行政区划划分区域道路网；\n\nb）按照功能区，如商圈、商务区、交通枢纽、场站、旅游景点等划分区域道路网；\n\nc）按照水系、山脉、道路等分割物划分区域道路网。",
        repair_log,
        "restored 5.3 list markers",
    )
    text = _replace_gb_section(
        text,
        r"(## 5\.5\.1 快速路路段划分\n\n).*?(?=\n## 5\.5\.2)",
        r"\1快速路应以出入口为端点进行分段，路段长度大于或等于3km应再分段。",
        repair_log,
        "restored 5.5.1 minimum segment length",
    )
    text = _replace_gb_section(
        text,
        r"(## 5\.5\.2 主干路、次干路、支路路段划分\n\n).*?(?=\n## 6 数据采集要求)",
        r"\1主干路、次干路、支路应以停车线为端点进行分段，即上游停车线到下游停车线为一个路段，路段长度大于或等于1.5km应再分段。",
        repair_log,
        "restored 5.5.2 minimum segment length",
    )
    text = _replace_gb_section(
        text,
        r"(## 7\.2\.1\.1 平均交通流量\n\n.*?\$\$\n\n)式中:.*?(?=\n## 7\.2\.1\.2)",
        r"\1式中：\n\n\\(\\overline{Q}\\) ——平均交通流量，单位为辆每小时（pcu/h），pcu为标准车辆数；\n\n\\(Q_k\\) ——第 k 个时间间隔的交通流量，单位为辆（pcu）；\n\nn ——时间间隔个数；\n\nτ ——时间间隔长，单位为小时（h）。",
        repair_log,
        "restored 7.2.1.1 variable definitions",
    )
    text = _replace_gb_section(
        text,
        r"(## 7\.2\.1\.2 自由流速度\n\n).*?(?=\n## 7\.2\.1\.3)",
        r"\1针对评价路段按如下步骤进行计算，单位为千米每小时（km/h）：\n\na）将6:00~24:00按给定时间间隔等分，其间隔长度不超过15min；\n\nb）计算每一时间间隔平均行程速度的算术平均值，样本天数应不少于30d；\n\nc）将计算出的平均值从大到小排序，取排序结果的前1/9进行平均，其结果作为路段自由流速度；\n\nd）当计算得到的自由流速度超过道路限速时取限速。",
        repair_log,
        "restored 7.2.1.2 calculation steps",
    )
    text = _replace_gb_section(
        text,
        r"(## 7\.2\.1\.3 平均行程速度\n\n.*?\$\$\n\n)式中:.*?(?=\n## 7\.2\.1\.4)",
        r"\1式中：\n\n\\(V_{kj}\\) ——时间间隔 k 内路段 j 的平均行程速度，单位为千米每小时（km/h）；\n\n\\(L_{kji}\\) ——时间间隔 k 内第 i 辆车在路段 j 上行驶的距离，单位为千米（km）；\n\n\\(t_{kji}\\) ——时间段 k 内第 i 辆车通过路段 j 的行程时间，单位为小时（h）；\n\nn ——观测行程时间的车次数。",
        repair_log,
        "restored 7.2.1.3 variable definitions",
    )
    text = _replace_once(
        text,
        "路段平均行程速度计算的最小间隔应不大于 ,计算方法见式(),",
        "路段平均行程速度计算的最小间隔应不大于5min，计算方法见式（2）。",
        repair_log,
        "restored 7.2.1.3 sampling interval",
    )
    text = _replace_gb_section(
        text,
        r"(## 7\.2\.1\.4 道路和道路网平均行程速度\n\n).*?(?=\n## 7\.2\.2)",
        r"\1道路和道路网的平均行程速度计算按GB/T 29107—2012附录A进行加权计算。\n\n高峰时段、日、周、月、季、年的平均行程速度采用算术平均计算。",
        repair_log,
        "restored 7.2.1.4 reference",
    )
    text = _replace_gb_section(
        text,
        r"(## 7\.2\.2\.1 行程时间比\n\n).*?(?=\n## 7\.2\.2\.2)",
        r"\1行程时间比值越大表示交通运行状况越差，即越拥堵。计算方法见式（3）。\n\n$$\n\\mathrm{TTI}_{kj} = \\frac{\\overline{t}_{kj}}{t_j^f}\\tag{3}\n$$\n\n式中：\n\n\\(TTI_{kj}\\) ——路段 j 在某一时间间隔 k 内的行程时间比，时间间隔应不大于15min（0.25h）；\n\n\\(\\overline{t}_{kj}\\) ——时间间隔 k 内车辆行驶过路段 j 所使用的平均时间，\\(\\overline{t}_{kj}=\\frac{\\sum_{i=1}^{n}t_{kji}}{n}\\) 或者 \\(\\overline{t}_{kj}=\\frac{L_j}{V_{kj}}\\)，n为车辆数，单位为小时（h）；\n\n\\(t_j^f\\) ——路段 j 在自由流状态下的行程时间，单位为小时（h）。\n\n当路段行程时间小于自由流行程时间时，设定TTI等于1。",
        repair_log,
        "restored 7.2.2.1 definition and interval",
    )
    text = _replace_gb_section(
        text,
        r"(## 7\.2\.2\.2 延误时间比\n\n).*?(?=\n## 7\.2\.2\.3)",
        r"\1延误时间比值越大表示交通运行状况越差，即越拥堵。计算方法见式（4），计算时间间隔应不大于15min（0.25h）。\n\n$$\n\\mathrm{DTP}_{kj} = \\frac{\\overline{t}_{kj}-t_j^f}{\\overline{t}_{kj}}\\tag{4}\n$$\n\n当路段实际行程时间小于自由流行程时间时，设定DTP等于0。",
        repair_log,
        "restored 7.2.2.2 definition and interval",
    )
    text = _replace_gb_section(
        text,
        r"(## 7\.2\.2\.3 运行状况等级里程比例\n\n).*?(?=\n## 7\.2\.2\.4)",
        r"\1选定时间段内，道路处于不同交通运行状况的空间测度。计算方法见式（5）。\n\n$$\nDP_{ki}=\\frac{\\sum_{j=1}^{n}m_{kji}}{\\sum_{j=1}^{n}L_j}\\times100\\%\\tag{5}\n$$\n\n式中：\n\n\\(DP_{ki}\\) ——时间间隔 k 内道路处于运行状况等级 i 的里程百分比，i为表3确定的交通运行状况等级；\n\n\\(m_{kji}\\) ——时间间隔 k 内路段 j 运行状况等级为 i 的里程，单位为千米（km），时间间隔应不大于15min（0.25h）；\n\nn ——道路包含的路段数量；\n\n\\(L_j\\) ——评价范围内的道路总里程，单位为千米（km）。",
        repair_log,
        "restored 7.2.2.3 variable definitions",
    )
    text = _replace_gb_section(
        text,
        r"(## 7\.2\.2\.4 道路和道路网特征性指标计算\n\n).*?(?=\n## 7\.2\.3)",
        r"\1道路和道路网的特征性指标计算按GB/T 29107—2012附录A进行加权计算。\n\n高峰时段、日、周、月、季、年的平均行程速度采用算术平均计算。",
        repair_log,
        "restored 7.2.2.4 reference",
    )
    text = _replace_gb_section(
        text,
        r"(## 7\.2\.3\.1 按照拥堵里程方法换算\n\n).*?(?=\n## 7\.2\.3\.2)",
        r"\1按照7.2.2.3计算运行状况等级里程比例中的严重拥堵里程比例，最小时间间隔应不大于15min，严重拥堵里程比例与城市交通运行指数的换算关系参见附录A。",
        repair_log,
        "restored 7.2.3.1 references and interval",
    )
    text = _replace_gb_section(
        text,
        r"(## 7\.2\.3\.2 按照行程时间方法换算\n\n).*?(?=\n## 7\.2\.3\.3)",
        r"\1按照7.2.2.1计算路段行程时间比，按照7.2.2.4计算道路网行程时间比，最小时间间隔应不大于15min，道路网行程时间比与城市交通运行指数的换算关系参见附录B。",
        repair_log,
        "restored 7.2.3.2 references and interval",
    )
    text = _replace_gb_section(
        text,
        r"(## 7\.2\.3\.3 按照延误时间方法换算\n\n).*?(?=\n## 8 运行状况等级划分)",
        r"\1按照7.2.2.2计算路段延误时间比，按照7.2.2.4计算道路网延误时间比，最小时间间隔应不大于15min，道路网延误时间比与城市交通运行指数的换算关系参见附录C。",
        repair_log,
        "restored 7.2.3.3 references and interval",
    )
    text = _replace_gb_section(
        text,
        r"(## 8\.1 路段交通运行状况等级划分\n\n).*?(?=\n表2 路段交通运行状况等级划分表)",
        r"\1路段交通运行状况等级按照路段平均行程速度与自由流速度的关系划分为如下五个等级：\n\na）路段平均行程速度大于自由流速度的70%为畅通等级；\n\nb）路段平均行程速度大于自由流速度的50%且小于或等于自由流速度的70%时为基本畅通等级；\n\nc）路段平均行程速度大于自由流速度的40%且小于或等于自由流速度的50%时为轻度拥堵等级；\n\nd）路段平均行程速度大于自由流速度的30%且小于或等于自由流速度的40%时为中度拥堵等级；\n\ne）当路段平均行程速度小于或等于自由流速度的30%时为严重拥堵等级。\n\n路段交通运行状况等级用颜色表示，颜色代码见附录D。等级划分和颜色表示应符合表2要求。\n\n",
        repair_log,
        "restored 8.1 traffic-grade thresholds",
    )
    text = _replace_once(
        text,
        "路网运行状况等级划分为五级 等级划分和颜色表示应符合表3要求",
        "路网运行状况等级划分为五级，等级划分和颜色表示应符合表3要求。",
        repair_log,
        "restored 8.2 grade sentence",
    )
    text = _replace_once(
        text,
        "路网运行状况等级划分为五级 等级划分和颜色表示应符合表 要求",
        "路网运行状况等级划分为五级，等级划分和颜色表示应符合表3要求。",
        repair_log,
        "restored 8.2 grade table reference",
    )
    return text


def _repair_industry_formulas(text: str, repair_log: list[str]) -> str:
    formula_pattern = re.compile(r"\$\$.*?\$\$", re.DOTALL)
    blocks = list(formula_pattern.finditer(text))
    if len(blocks) != len(INDUSTRY_FORMULA_TAGS):
        repair_log.append(
            "formula count differs from expected B.1-B.26 and C.1 sequence; manual review required"
        )
        return text

    pieces: list[str] = []
    cursor = 0
    for index, match in enumerate(blocks):
        pieces.append(text[cursor:match.start()])
        block = match.group(0)
        expected = INDUSTRY_FORMULA_TAGS[index]
        if re.search(r"\\tag\{", block):
            block = re.sub(
                r"\\tag\{[^}]*\}",
                rf"\\tag{{{expected}}}",
                block,
                count=1,
            )
        else:
            block = block[:-2].rstrip() + rf"\\tag{{{expected}}}" + "\n$$"
        pieces.append(block)
        cursor = match.end()
    pieces.append(text[cursor:])
    normalized = "".join(pieces)
    normalized, count = re.subn(
        r"\n\s*\((?:B|C)\.\s*\d+\)\s*\n",
        "\n",
        normalized,
    )
    repair_log.append(
        f"normalized {len(blocks)} formula tags and removed {count} duplicated labels"
    )
    return normalized


def _normalize_body(
    body_lines: list[str],
    spec: DocumentSpec,
) -> tuple[str, list[str], dict[str, int]]:
    repair_log: list[str] = []
    lines: list[str] = []
    image_count = 0
    standalone_ellipsis_count = 0
    for raw_line in body_lines:
        line = raw_line.replace("\ufeff", "").replace("\u00a0", " ").rstrip()
        line = _normalize_heading(line, spec)
        line = _normalize_eq_tags(line, repair_log)
        if re.match(r"^!\[[^]]*\]\(https?://", line.strip()):
            image_count += 1
            repair_log.append("removed remote MinerU image link from text corpus")
            lines.append("<!-- 图示未进入文本检索；原始 PDF 保留该图。 -->")
            continue
        if line.strip() in {"......", "……", ". . . . . . . . . . . . . . . . . . . ."}:
            standalone_ellipsis_count += 1
            continue
        lines.append(line)

    if spec.key == "gb_t_33171_2016":
        lines = _merge_gb_t_33171_term_headings(lines)
        lines = _promote_gb_t_33171_subheadings(lines)

    text = "\n".join(lines)
    if spec.key == "xiongan_planning_outline":
        text = _repair_xiongan(text, repair_log)
    elif spec.key == "gb_t_33171_2016":
        text = _repair_gb_t_33171(text, repair_log)
    elif spec.key == "ga_t_527_2_2024":
        text = _repair_industry_formulas(text, repair_log)

    # Keep the semantic layout but avoid large blank runs after removed
    # administrative artifacts or image-only lines.
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    metrics = {
        "image_links_removed": image_count,
        "standalone_ellipsis_lines_removed": standalone_ellipsis_count,
    }
    return text, repair_log, metrics


def _frontmatter(spec: DocumentSpec, source_pdf: Path, source_json: Path, page_count: int) -> str:
    metadata = {
        "title": spec.title,
        "document_type": spec.document_type,
        "profile": spec.profile,
        "authority": spec.authority,
        "document_number": spec.document_number,
        "status": spec.status,
        "source_filename": spec.source_filename,
        "source_pdf": str(source_pdf.resolve()),
        "source_json": str(source_json.resolve()),
        "source_page_mapping": "MinerU JSON page_idx; original PDF is authoritative",
        "page_count": str(page_count),
        "normalized_date": str(date.today()),
    }
    lines = ["---"]
    for key, value in metadata.items():
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    lines.extend(
        [
            "---",
            "",
            f"# {spec.title}",
            "",
            "> 本文为 MinerU Markdown 的非破坏性清洗视图；封面、目录、前言和参考文献未作为知识正文保留。原始 PDF、MinerU Markdown 与 JSON 均保留在源目录。",
            "",
        ]
    )
    return "\n".join(lines)


def _quality_warnings(text: str, spec: DocumentSpec) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    patterns: list[tuple[str, str]] = [
        ("replacement_character", "发现 U+FFFD 替换字符"),
        ("missing_standard_reference", "发现疑似缺失的标准编号"),
        ("unresolved_numeric_heading", "发现未修复的纯数字章节编号"),
        ("malformed_formula_tag", "发现未修复的公式编号占位符"),
        ("blank_numeric_value", "发现疑似缺失的数值"),
    ]
    for code, message in patterns:
        matched = False
        if code == "replacement_character":
            matched = "\ufffd" in text
        elif code == "missing_standard_reference":
            matched = bool(re.search(r"/\s*[—-]", text))
        elif code == "unresolved_numeric_heading":
            matched = bool(re.search(r"(?m)^##\s+\d{2,4}\s*$", text))
        elif code == "malformed_formula_tag":
            matched = bool(re.search(r"\\tag\{[^}]*\.{2,}", text))
        elif code == "blank_numeric_value":
            matched = bool(
                re.search(
                    r"(?:取值范围为|达到|不超过|不少于|大于|小于)\s*[。；,，：:]",
                    text,
                )
            )
        if matched:
            warnings.append({"code": code, "message": message})

    if spec.key == "ga_t_527_2_2024":
        warnings.append(
            {
                "code": "draft_status",
                "message": "该文档来自报批稿，回答时必须标注“以正式发布稿为准”。",
            }
        )
    warnings.append(
        {
            "code": "page_mapping_pending",
            "message": "清洗版暂未给每个 chunk 写入页码；已保留 JSON 路径和页数，后续构建索引时补充页码映射。",
        }
    )
    return warnings


def _markdown_metrics(text: str) -> dict[str, int]:
    return {
        "chars": len(text),
        "lines": len(text.splitlines()),
        "headings": len(re.findall(r"(?m)^#{1,6}\s+", text)),
        "html_tables": text.count("<table>"),
        "latex_delimiters": text.count("$$"),
        "inline_latex": text.count("\\(") + text.count("$") - text.count("$$") * 2,
        "replacement_characters": text.count("\ufffd"),
    }


def _write_readme(output_dir: Path) -> None:
    readme = """# MinerU 清洗版知识源

本目录由 `scripts/standards/normalize_mineru_markdown.py` 生成，属于原始 MinerU 导出的非破坏性派生视图。

- 仅保留正文和附录；封面、目录、前言、参考文献不进入正文检索。
- 标准编号、发布机构、状态和原文件路径保存在 Markdown frontmatter 中。
- 原始 PDF、MinerU Markdown 和 MinerU JSON 不在此目录中修改。
- `quality.json` 中的警告必须在建立正式 RAG 索引前处理或明确接受。
- 行业标准 `GA/T 527.2-2024` 当前是报批稿，不应当作无条件的正式发布稿引用。
- JSON 仅用于页码/版面追溯，不直接作为 embedding 文本。
"""
    (output_dir / "README.md").write_text(readme, encoding="utf-8", newline="\n")


def normalize_one(
    markdown_path: Path,
    input_dir: Path,
    output_dir: Path,
    source_root: Path,
) -> dict[str, Any]:
    spec = _find_spec(markdown_path)
    json_path = _find_json(input_dir, spec)
    source_pdf = _find_source_pdf(source_root, spec)
    json_summary = _json_summary(json_path)
    raw_lines = markdown_path.read_text(encoding="utf-8").replace("\r\n", "\n").splitlines()
    body_lines, body_start_line, body_end_line = _split_body(raw_lines, spec)
    body, repair_log, cleanup_metrics = _normalize_body(body_lines, spec)
    output_path = output_dir / spec.output_name
    output_path.write_text(
        _frontmatter(spec, source_pdf, json_path, json_summary["page_count"])
        + body
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    warnings = _quality_warnings(body, spec)
    report = {
        "key": spec.key,
        "title": spec.title,
        "input_markdown": str(markdown_path.resolve()),
        "output_markdown": str(output_path.resolve()),
        "source_pdf": str(source_pdf.resolve()),
        "source_json": str(json_path.resolve()),
        "raw_line_count": len(raw_lines),
        "removed_prefix_line_count": body_start_line - 1,
        "removed_suffix_line_count": max(0, len(raw_lines) - body_end_line),
        "body_source_line_range": [body_start_line, body_end_line],
        "json": json_summary,
        "cleanup": cleanup_metrics,
        "repairs": repair_log,
        "markdown": _markdown_metrics(body),
        "warnings": warnings,
        "quality_status": "needs_manual_review" if warnings else "passed",
    }
    (output_dir / f"{output_path.name}.quality.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--source-root",
        type=Path,
        default=None,
        help="Directory containing the original PDFs; defaults to input-dir/..",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    source_root = (args.source_root or input_dir.parent).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    markdown_paths = sorted(input_dir.glob("*.md"))
    expected_markers = {spec.filename_marker for spec in SPECS}
    found_markers = {spec.filename_marker for path in markdown_paths for spec in SPECS if spec.filename_marker in path.name}
    missing = expected_markers - found_markers
    if missing:
        raise SystemExit(f"Missing MinerU Markdown documents: {sorted(missing)}")

    reports = [normalize_one(path, input_dir, output_dir, source_root) for path in markdown_paths]
    _write_readme(output_dir)
    manifest = {
        "generated_date": str(date.today()),
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "source_root": str(source_root),
        "documents": reports,
    }
    (output_dir / "normalization_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(
        {
            "output_dir": str(output_dir),
            "documents": len(reports),
            "quality_statuses": Counter(report["quality_status"] for report in reports),
            "warnings": sum(len(report["warnings"]) for report in reports),
        },
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
