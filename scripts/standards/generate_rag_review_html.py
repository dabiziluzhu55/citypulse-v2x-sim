"""Generate a human-readable review page for prepared standards RAG sources.

This tool intentionally does not load an embedding model or Chroma.  It checks
whether the prepared chunks contain representative metric definitions and
calculation/grading sections, then embeds the chunks in a local HTML viewer.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


DEFAULT_NORMALIZED_DIR = (
    Path(__file__).resolve().parents[4]
    / "repos"
    / "国家与行业标准文件"
    / "MinerU-normalized"
)


METRIC_INVENTORY = [
    # GB/T 33171-2016 terminology definitions.
    ("交通运行状况", "standard_gbt33171_2016", "3.1"),
    ("路段", "standard_gbt33171_2016", "3.2"),
    ("交通流量", "standard_gbt33171_2016", "3.3"),
    ("行程时间", "standard_gbt33171_2016", "3.4"),
    ("行程速度", "standard_gbt33171_2016", "3.5"),
    ("自由流速度", "standard_gbt33171_2016", "3.6"),
    ("自由流行程时间", "standard_gbt33171_2016", "3.7"),
    ("行程时间比", "standard_gbt33171_2016", "3.8"),
    ("延误时间", "standard_gbt33171_2016", "3.9"),
    ("延误时间比", "standard_gbt33171_2016", "3.10"),
    ("运行状况等级里程比例", "standard_gbt33171_2016", "3.11"),
    ("城市交通运行指数", "standard_gbt33171_2016", "3.12"),
    ("车公里数", "standard_gbt33171_2016", "3.13"),
    # GA/T 527.2-2024 Appendix B metric definitions.
    ("绿灯间隔清空率", "industry_standard_gat527_2_2024", "B.1"),
    ("行人过街保障率", "industry_standard_gat527_2_2024", "B.2"),
    ("溢流率", "industry_standard_gat527_2_2024", "B.3"),
    ("协调速度变异系数", "industry_standard_gat527_2_2024", "B.4"),
    ("交叉口停车次数", "industry_standard_gat527_2_2024", "B.5"),
    ("绿灯利用率", "industry_standard_gat527_2_2024", "B.6"),
    ("平均控制延误", "industry_standard_gat527_2_2024", "B.7"),
    ("协调方向平均行程速度", "industry_standard_gat527_2_2024", "B.8"),
    ("协调方向单位绿灯通过量", "industry_standard_gat527_2_2024", "B.9"),
    ("协调方向不停车通过率", "industry_standard_gat527_2_2024", "B.10"),
    ("协调队列比", "industry_standard_gat527_2_2024", "B.11"),
    ("区域最大排队长度", "industry_standard_gat527_2_2024", "B.12"),
    ("路径平均速度", "industry_standard_gat527_2_2024", "B.13"),
    ("路径停车次数", "industry_standard_gat527_2_2024", "B.14"),
    ("行人过街平均延误", "industry_standard_gat527_2_2024", "B.15"),
    ("公交平均行程速度", "industry_standard_gat527_2_2024", "B.16"),
    ("公交平均控制延误", "industry_standard_gat527_2_2024", "B.17"),
    ("公交不停车通过率", "industry_standard_gat527_2_2024", "B.18"),
    ("红初最大排队比", "industry_standard_gat527_2_2024", "B.19"),
    ("协调路段排队长度空间占比", "industry_standard_gat527_2_2024", "B.20"),
    ("非协调方向最大排队长度", "industry_standard_gat527_2_2024", "B.21"),
    ("失衡系数", "industry_standard_gat527_2_2024", "B.22"),
    ("社会车辆最大排队长度", "industry_standard_gat527_2_2024", "B.23"),
]


QUERY_TESTS = [
    {
        "id": "mean_trip_speed",
        "query": "平均行程速度是什么意思？",
        "terms": ["平均行程速度"],
        "document_id": "standard_gbt33171_2016",
        "section": "7.2.1.3",
    },
    {
        "id": "travel_time_ratio",
        "query": "行程时间比如何计算？",
        "terms": ["行程时间比"],
        "document_id": "standard_gbt33171_2016",
        "section": "7.2.2.1",
    },
    {
        "id": "traffic_index",
        "query": "城市交通运行指数怎么换算？",
        "terms": ["城市交通运行指数"],
        "document_id": "standard_gbt33171_2016",
        "section": "7.2.3",
    },
    {
        "id": "congestion_grade",
        "query": "道路网交通运行状况等级如何划分？",
        "terms": ["道路网交通运行状况等级"],
        "document_id": "standard_gbt33171_2016",
        "section": "8.2",
    },
    {
        "id": "congestion_color",
        "query": "交通运行状况等级的颜色如何表示？",
        "terms": ["颜色表示"],
        "document_id": "standard_gbt33171_2016",
        "section": "附录D",
    },
    {
        "id": "overflow_rate",
        "query": "溢流率是什么意思？",
        "terms": ["溢流率"],
        "document_id": "industry_standard_gat527_2_2024",
        "section": "B.3",
    },
    {
        "id": "queue_length",
        "query": "区域最大排队长度是什么意思？",
        "terms": ["区域最大排队长度"],
        "document_id": "industry_standard_gat527_2_2024",
        "section": "B.12",
    },
    {
        "id": "unbalance_coefficient",
        "query": "失衡系数如何理解？",
        "terms": ["失衡系数"],
        "document_id": "industry_standard_gat527_2_2024",
        "section": "B.22",
    },
    {
        "id": "evaluation_indicators",
        "query": "信号控制效益有哪些评估指标？",
        "terms": ["控制效益评估指标"],
        "document_id": "industry_standard_gat527_2_2024",
        "section": "5.2",
    },
    {
        "id": "xiongan_green_smart_transport",
        "query": "雄安新区规划中的绿色智能交通是什么？",
        "terms": ["绿色智能交通"],
        "document_id": "policy_xiongan_planning_outline_2018",
        "section": "第七章",
    },
]


CALCULATION_SECTIONS = [
    "7.2.1.1",
    "7.2.1.2",
    "7.2.1.3",
    "7.2.1.4",
    "7.2.2.1",
    "7.2.2.2",
    "7.2.2.3",
    "7.2.2.4",
    "7.2.3.1",
    "7.2.3.2",
    "7.2.3.3",
]


def normalize(value: Any) -> str:
    """Normalize whitespace for robust matching of MinerU headings."""

    return re.sub(r"\s+", "", str(value or "")).lower()


def load_chunks(normalized_dir: Path) -> list[dict[str, Any]]:
    path = normalized_dir / "rag_build" / "chunks.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def ref(row: dict[str, Any], document_titles: dict[str, str]) -> dict[str, Any]:
    return {
        "chunk_id": row["chunk_id"],
        "document": document_titles.get(row["document_id"], row["document_id"]),
        "document_id": row["document_id"],
        "section": row.get("section", ""),
        "pages": row.get("source_pages", []),
        "printed_pages": row.get("printed_pages", []),
        "chunk_type": row.get("chunk_type", "text"),
    }


def metric_coverage(
    rows: list[dict[str, Any]], document_titles: dict[str, str]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for term, document_id, section_prefix in METRIC_INVENTORY:
        term_n = normalize(term)
        section_n = normalize(section_prefix)
        text_hits = [row for row in rows if term_n in normalize(row.get("text"))]
        definition_hits = [
            row
            for row in text_hits
            if row["document_id"] == document_id
            and section_n in normalize(row.get("section"))
        ]
        sample = definition_hits[0] if definition_hits else (text_hits[0] if text_hits else None)
        result.append(
            {
                "term": term,
                "document_id": document_id,
                "section_prefix": section_prefix,
                "text_hit_count": len(text_hits),
                "definition_hit_count": len(definition_hits),
                "pass": bool(definition_hits),
                "sample": ref(sample, document_titles) if sample else None,
            }
        )
    return result


def query_coverage(
    rows: list[dict[str, Any]], document_titles: dict[str, str]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for test in QUERY_TESTS:
        terms_n = [normalize(term) for term in test["terms"]]
        expected_section_n = normalize(test.get("section"))
        matches = []
        expected_matches = []
        for row in rows:
            text_n = normalize(row.get("text"))
            score = sum(term in text_n for term in terms_n)
            if score == 0:
                continue
            if normalize(row.get("section")) == expected_section_n:
                score += 3
            elif expected_section_n and expected_section_n in normalize(row.get("section")):
                score += 2
            if row["document_id"] == test["document_id"]:
                score += 2
            matches.append((score, row))
            if (
                row["document_id"] == test["document_id"]
                and (not expected_section_n or expected_section_n in normalize(row.get("section")))
            ):
                expected_matches.append(row)
        matches.sort(key=lambda item: (-item[0], item[1]["chunk_id"]))
        result.append(
            {
                **test,
                "pass": bool(expected_matches),
                "hit_count": len(matches),
                "expected_hit_count": len(expected_matches),
                "top_hits": [ref(row, document_titles) for _, row in matches[:3]],
            }
        )
    return result


def section_coverage(
    rows: list[dict[str, Any]], document_titles: dict[str, str], sections: list[dict[str, str]]
) -> list[dict[str, Any]]:
    result = []
    for item in sections:
        prefix_n = normalize(item["prefix"])
        hits = [
            row
            for row in rows
            if row["document_id"] == item["document_id"]
            and (
                prefix_n in normalize(row.get("section"))
                if item.get("match_mode") == "contains"
                else normalize(row.get("section")).startswith(prefix_n)
            )
        ]
        result.append(
            {
                **item,
                "pass": bool(hits),
                "hit_count": len(hits),
                "sample": ref(hits[0], document_titles) if hits else None,
            }
        )
    return result


def build_payload(normalized_dir: Path) -> dict[str, Any]:
    manifest = json.loads((normalized_dir / "rag_manifest.json").read_text(encoding="utf-8"))
    report = json.loads(
        (normalized_dir / "rag_build" / "build_report.json").read_text(encoding="utf-8")
    )
    rows = load_chunks(normalized_dir)
    document_titles = {doc["id"]: doc["title"] for doc in manifest["documents"]}
    metrics = metric_coverage(rows, document_titles)
    queries = query_coverage(rows, document_titles)
    calculation = section_coverage(
        rows,
        document_titles,
        [
            {
                "prefix": prefix,
                "document_id": "standard_gbt33171_2016",
                "label": "GB/T 33171 计算方法 " + prefix,
            }
            for prefix in CALCULATION_SECTIONS
        ],
    )
    policy = section_coverage(
        rows,
        document_titles,
        [
            {
                "prefix": "第七章",
                "document_id": "policy_xiongan_planning_outline_2018",
                "label": "雄安规划交通章节",
            },
            {
                "prefix": "第三节 打造绿色智能交通系统",
                "document_id": "policy_xiongan_planning_outline_2018",
                "label": "绿色智能交通系统",
                "match_mode": "contains",
            },
        ],
    )
    return {
        "generated_at": report.get("generated_at"),
        "report": report,
        "manifest": manifest,
        "rows": rows,
        "metrics": metrics,
        "queries": queries,
        "calculation": calculation,
        "policy": policy,
        "notes": {
            "retrieval_mode": "exact-text coverage check only",
            "embedding_built": False,
            "chroma_built": False,
            "source_note": "Markdown supplies text; matching MinerU JSON supplies page provenance.",
        },
    }


HTML_TEMPLATE = r'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CityPulse 标准知识库切片检查</title>
  <style>
    :root { color-scheme: light; --ink:#1f2937; --muted:#667085; --line:#e5e7eb; --panel:#fff; --bg:#f4f6f8; --blue:#2563eb; --green:#0f8a5f; --red:#b42318; --amber:#a15c00; }
    * { box-sizing: border-box; }
    body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif; }
    .wrap { width:min(1440px,calc(100% - 36px)); margin:24px auto 48px; }
    h1 { margin:0 0 6px; font-size:26px; letter-spacing:.01em; }
    h2 { margin:0 0 14px; font-size:18px; }
    h3 { margin:0 0 8px; font-size:15px; }
    .sub { color:var(--muted); margin-bottom:18px; }
    .panel { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:18px; margin:16px 0; box-shadow:0 2px 8px rgba(16,24,40,.04); }
    .cards { display:grid; grid-template-columns:repeat(5,minmax(130px,1fr)); gap:12px; }
    .card { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px; }
    .card .num { display:block; font-size:24px; font-weight:700; }
    .card .label { color:var(--muted); }
    .controls { display:grid; grid-template-columns:minmax(280px,2fr) 1fr 1fr 1fr; gap:10px; }
    input,select { width:100%; border:1px solid #cfd4dc; border-radius:8px; padding:9px 10px; background:#fff; color:var(--ink); }
    .quick { display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; }
    button { border:1px solid #cfd4dc; border-radius:999px; padding:7px 11px; background:#fff; color:#344054; cursor:pointer; }
    button:hover { border-color:var(--blue); color:var(--blue); }
    .table-wrap { overflow:auto; }
    table { border-collapse:collapse; width:100%; min-width:720px; }
    th,td { text-align:left; vertical-align:top; border-bottom:1px solid var(--line); padding:9px 8px; }
    th { background:#f8fafc; color:#475467; font-weight:600; position:sticky; top:0; }
    .ok { color:var(--green); font-weight:700; }
    .bad { color:var(--red); font-weight:700; }
    .warn { color:var(--amber); }
    .pill { display:inline-block; padding:2px 7px; border-radius:999px; background:#eef2ff; color:#3730a3; font-size:12px; margin:1px 3px 1px 0; }
    .ref { color:#475467; font-size:12px; }
    .ref code { color:#344054; }
    .result { border-top:1px solid var(--line); padding:14px 0; }
    .result:first-child { border-top:0; padding-top:0; }
    .result-head { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-bottom:5px; }
    .result-title { font-weight:700; }
    pre { white-space:pre-wrap; word-break:break-word; background:#f8fafc; border:1px solid #edf0f3; border-radius:8px; padding:11px; margin:8px 0 0; max-height:280px; overflow:auto; font:13px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace; }
    .muted { color:var(--muted); }
    .notice { background:#fffaeb; border:1px solid #fedf89; color:#7a4d00; border-radius:8px; padding:10px 12px; margin-top:10px; }
    .two { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
    footer { color:var(--muted); font-size:12px; margin-top:18px; }
    @media (max-width:900px) { .cards{grid-template-columns:repeat(2,1fr)} .controls{grid-template-columns:1fr 1fr} .two{grid-template-columns:1fr} }
    @media (max-width:560px) { .wrap{width:min(100% - 20px,1440px);margin:12px auto 30px} .cards{grid-template-columns:1fr 1fr} .controls{grid-template-columns:1fr} }
  </style>
</head>
<body>
  <main class="wrap">
    <h1>CityPulse 标准知识库切片检查</h1>
    <div class="sub">用于人工检查 manifest、章节/表格切片和元数据覆盖。生成日期：<span id="generated"></span></div>
    <div class="cards" id="cards"></div>

    <section class="panel">
      <h2>快速检查</h2>
      <div class="controls">
        <input id="search" placeholder="输入指标名称，例如：平均行程速度、溢流率、交通指数">
        <select id="profile"><option value="">全部 profile</option></select>
        <select id="document"><option value="">全部文档</option></select>
        <select id="type"><option value="">全部类型</option><option value="text">正文</option><option value="table">表格</option></select>
      </div>
      <div class="quick" id="quick"></div>
      <div class="muted" style="margin-top:10px">当前浏览器搜索是本地文本定位，不代表最终 Embedding/Chroma 召回排序。</div>
    </section>

    <section class="panel">
      <h2>覆盖检查</h2>
      <div id="coverage-summary"></div>
      <div class="two">
        <div>
          <h3>代表性问答定位</h3>
          <div class="table-wrap"><table><thead><tr><th>结果</th><th>问题</th><th>命中</th><th>对应块</th></tr></thead><tbody id="query-table"></tbody></table></div>
        </div>
        <div>
          <h3>计算方法和规划章节</h3>
          <div class="table-wrap"><table><thead><tr><th>结果</th><th>章节</th><th>命中</th><th>页码</th></tr></thead><tbody id="section-table"></tbody></table></div>
        </div>
      </div>
      <details style="margin-top:16px"><summary>展开查看全部 36 个指标定义覆盖</summary><div class="table-wrap" style="margin-top:10px"><table><thead><tr><th>结果</th><th>指标</th><th>文档</th><th>预期章节</th><th>文本命中</th><th>定义块</th><th>示例</th></tr></thead><tbody id="metric-table"></tbody></table></div></details>
    </section>

    <section class="panel">
      <h2>切片浏览</h2>
      <div id="results" class="muted">请输入指标名称，或点击上面的快速问题。</div>
    </section>

    <div class="notice">说明：本页只检查“清洗后的知识是否进入了正确切片，并能通过文本定位找到”。Embedding、Chroma 和 Qwen 问答尚未在本页执行。</div>
    <footer>文本来源：MinerU-normalized Markdown；页码来源：匹配的 MinerU JSON。原始 PDF 仍是正式页码和内容核验依据。</footer>
  </main>
  <script>
    const DATA = __DATA__;
    const rows = DATA.rows || [];
    const docs = Object.fromEntries((DATA.manifest.documents || []).map(d => [d.id, d]));
    const metricTerms = (DATA.metrics || []).map(x => x.term);
    const queryTerms = (DATA.queries || []).flatMap(x => x.terms || []);
    const knownTerms = [...new Set([...metricTerms, ...queryTerms])].sort((a,b) => b.length - a.length);
    const norm = value => String(value ?? '').replace(/\s+/g, '').toLowerCase();
    const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    const pages = row => (row.source_pages || []).join(', ') || '-';
    const printed = row => (row.printed_pages || []).join(', ') || '-';

    document.getElementById('generated').textContent = DATA.generated_at || '-';
    const report = DATA.report || {};
    const cards = [
      ['文档', report.document_count ?? (DATA.manifest.documents || []).length],
      ['切片', report.chunk_count ?? rows.length],
      ['表格切片', report.table_chunk_count ?? rows.filter(x => x.chunk_type === 'table').length],
      ['页码未解析', report.unresolved_page_mapping_count ?? '-'],
      ['指标定义覆盖', `${(DATA.metrics || []).filter(x => x.pass).length}/${(DATA.metrics || []).length}`],
    ];
    document.getElementById('cards').innerHTML = cards.map(([label,num]) => `<div class="card"><span class="num">${esc(num)}</span><span class="label">${esc(label)}</span></div>`).join('');

    const profileSelect = document.getElementById('profile');
    [...new Set(rows.map(x => x.profile).filter(Boolean))].sort().forEach(value => profileSelect.insertAdjacentHTML('beforeend', `<option value="${esc(value)}">${esc(value)}</option>`));
    const documentSelect = document.getElementById('document');
    Object.values(docs).forEach(doc => documentSelect.insertAdjacentHTML('beforeend', `<option value="${esc(doc.id)}">${esc(doc.title)}</option>`));

    const quick = document.getElementById('quick');
    (DATA.queries || []).forEach(test => {
      const btn = document.createElement('button');
      btn.textContent = test.query;
      btn.addEventListener('click', () => { document.getElementById('search').value = test.terms.join(' '); renderResults(); });
      quick.appendChild(btn);
    });

    function rowScore(row, query) {
      const q = norm(query);
      if (!q) return 0;
      const text = norm(row.text);
      const section = norm(row.section);
      const terms = knownTerms.filter(term => q.includes(norm(term)));
      const tokens = terms.length ? terms : q.split(/[^\u4e00-\u9fffA-Za-z0-9_.-]+/).filter(x => x.length > 1);
      let score = 0;
      tokens.forEach(term => { if (text.includes(norm(term))) score += norm(term).length > 3 ? 3 : 1; });
      if (section.includes(q)) score += 5;
      if (text.includes(q)) score += 5;
      return score;
    }

    function renderResults() {
      const query = document.getElementById('search').value.trim();
      const profile = document.getElementById('profile').value;
      const documentId = document.getElementById('document').value;
      const type = document.getElementById('type').value;
      let filtered = rows.filter(row => (!profile || row.profile === profile) && (!documentId || row.document_id === documentId) && (!type || row.chunk_type === type));
      if (query) filtered = filtered.map(row => ({row, score: rowScore(row, query)})).filter(x => x.score > 0).sort((a,b) => b.score - a.score || a.row.chunk_id.localeCompare(b.row.chunk_id)).slice(0, 50).map(x => x.row);
      else filtered = filtered.slice(0, 20);
      const target = document.getElementById('results');
      if (!filtered.length) { target.innerHTML = '<div class="muted">没有找到匹配切片。注意：这只是文本定位，不代表向量索引结果。</div>'; return; }
      target.innerHTML = filtered.map(row => {
        const doc = docs[row.document_id] || {};
        return `<article class="result"><div class="result-head"><span class="result-title">${esc(row.section || '未命名章节')}</span><span class="pill">${esc(row.chunk_type === 'table' ? '表格' : '正文')}</span><span class="pill">${esc(row.profile || '')}</span></div><div class="ref">${esc(doc.title || row.document_id)} · 标准编号：${esc(row.standard_number || '—')} · 状态：${esc(row.document_status || '—')} · PDF页：${esc(pages(row))} · 印刷页：${esc(printed(row))}</div><pre>${esc(row.text)}</pre><div class="ref">chunk_id：<code>${esc(row.chunk_id)}</code></div></article>`;
      }).join('');
    }
    ['search','profile','document','type'].forEach(id => document.getElementById(id).addEventListener('input', renderResults));

    const queryTable = document.getElementById('query-table');
    queryTable.innerHTML = (DATA.queries || []).map(test => {
      const top = (test.top_hits || [])[0];
      const status = test.pass ? '<span class="ok">通过</span>' : '<span class="bad">未找到</span>';
      const refText = top ? `${esc(top.section)} · PDF页 ${esc((top.pages || []).join(', '))}` : '—';
      return `<tr><td>${status}</td><td>${esc(test.query)}</td><td>${esc(test.hit_count)}（对应 ${esc(test.expected_hit_count)}）</td><td>${refText}</td></tr>`;
    }).join('');

    const sectionChecks = [...(DATA.calculation || []), ...(DATA.policy || [])];
    document.getElementById('section-table').innerHTML = sectionChecks.map(item => {
      const status = item.pass ? '<span class="ok">通过</span>' : '<span class="bad">未找到</span>';
      return `<tr><td>${status}</td><td>${esc(item.label || item.prefix)}</td><td>${esc(item.hit_count)}</td><td>${item.sample ? esc((item.sample.pages || []).join(', ')) : '—'}</td></tr>`;
    }).join('');

    document.getElementById('metric-table').innerHTML = (DATA.metrics || []).map(item => {
      const status = item.pass ? '<span class="ok">通过</span>' : '<span class="bad">缺失</span>';
      const sample = item.sample ? `${esc(item.sample.section)} · PDF页 ${esc((item.sample.pages || []).join(', '))}` : '—';
      return `<tr><td>${status}</td><td>${esc(item.term)}</td><td>${esc((docs[item.document_id] || {}).title || item.document_id)}</td><td>${esc(item.section_prefix)}</td><td>${esc(item.text_hit_count)}</td><td>${esc(item.definition_hit_count)}</td><td>${sample}</td></tr>`;
    }).join('');

    const queryPassed = (DATA.queries || []).filter(x => x.pass).length;
    const metricPassed = (DATA.metrics || []).filter(x => x.pass).length;
    const sectionPassed = sectionChecks.filter(x => x.pass).length;
    document.getElementById('coverage-summary').innerHTML = `<p><span class="ok">${metricPassed}/${(DATA.metrics || []).length}</span> 个指标定义有对应章节块；<span class="ok">${queryPassed}/${(DATA.queries || []).length}</span> 个代表性问题能定位到预期文档/章节；计算方法与规划检查 <span class="ok">${sectionPassed}/${sectionChecks.length}</span>。</p>`;
    document.getElementById('search').value = '平均行程速度';
    renderResults();
  </script>
</body>
</html>
'''


def render_html(payload: dict[str, Any]) -> str:
    # Prevent an accidental closing script tag if future source text contains it.
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return HTML_TEMPLATE.replace("__DATA__", data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--normalized-dir",
        type=Path,
        default=DEFAULT_NORMALIZED_DIR,
        help="MinerU-normalized directory",
    )
    parser.add_argument("--output", type=Path, default=None, help="HTML output path")
    args = parser.parse_args()

    normalized_dir = args.normalized_dir.resolve()
    output = (args.output or normalized_dir / "rag_build" / "rag_review.html").resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = build_payload(normalized_dir)
    output.write_text(render_html(payload), encoding="utf-8")

    metrics_passed = sum(item["pass"] for item in payload["metrics"])
    queries_passed = sum(item["pass"] for item in payload["queries"])
    sections = payload["calculation"] + payload["policy"]
    sections_passed = sum(item["pass"] for item in sections)
    print(
        json.dumps(
            {
                "output": str(output),
                "metric_definition_coverage": f"{metrics_passed}/{len(payload['metrics'])}",
                "query_coverage": f"{queries_passed}/{len(payload['queries'])}",
                "calculation_and_policy_coverage": f"{sections_passed}/{len(sections)}",
                "retrieval_mode": payload["notes"]["retrieval_mode"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
