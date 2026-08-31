#!/usr/bin/env python3
"""Static consistency audit for traffic_knowledge. Does not modify business code."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

KB_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = KB_ROOT.parent
REPORT_NAMES = {
    "KNOWLEDGE_UPDATE_REPORT.md",
    "KNOWLEDGE_AUDIT_V2.md",
}
STALE_PHRASES = [
    "recommended_control_mode",
    "LLM可推荐",
    "LLM只推荐算法",
    "STGCN 20节点",
    "当前代码没有把东部场景标为校园",
    "LLM选择控制算法",
    "LLM仅推荐control_mode",
    "LLM不参与实际交通管控",
    "LLM只是问答模型",
]
CONFLICT_ALLOW_IN_REPORTS = True

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


def emit(status: str, check: str, detail: str) -> str:
    print(f"{status}\t{check}\t{detail}")
    return status


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_string_tuple_assignment(source: str, name: str) -> list[str] | None:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == name:
            return _const_tuple(node.value)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if getattr(target, "id", None) == name:
                    return _const_tuple(node.value)
    return None


def _const_tuple(value: ast.AST | None) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, ast.Tuple):
        out = []
        for elt in value.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                out.append(elt.value)
            else:
                return None
        return out
    return None


def control_modes_from_registry(path: Path) -> list[str]:
    tree = ast.parse(read_text(path))
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign):
            continue
        if getattr(node.target, "id", None) != "CONTROL_MODE_REGISTRY":
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        modes = []
        for key in node.value.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                modes.append(key.value)
        return modes
    raise RuntimeError(f"CONTROL_MODE_REGISTRY not found in {path}")


def presets_from_py(path: Path) -> dict[str, dict[str, object]]:
    tree = ast.parse(read_text(path))
    registry = None
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "SCENARIO_PRESET_REGISTRY":
            registry = node.value
    if not isinstance(registry, ast.Dict):
        raise RuntimeError("SCENARIO_PRESET_REGISTRY not found")
    out: dict[str, dict[str, object]] = {}
    for key, value in zip(registry.keys, registry.values):
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            continue
        if not isinstance(value, ast.Call):
            continue
        fields: dict[str, object] = {}
        for kw in value.keywords:
            if kw.arg == "label" and isinstance(kw.value, ast.Constant):
                fields["label"] = kw.value.value
            elif kw.arg == "intersection_ids":
                ids = _const_tuple(kw.value)
                if ids is None and isinstance(kw.value, ast.Name) and kw.value.id == "ALL_DEMO_INTERSECTION_IDS":
                    ids = [f"demo_{i}" for i in range(1, 21)]
                fields["intersection_ids"] = ids
        out[key.value] = fields
    return out


def event_types_from_session(path: Path) -> list[str] | None:
    source = read_text(path)
    match = re.search(
        r"event_types:\s*tuple\[str,\s*\.\.\.\]\s*=\s*\((.*?)\)",
        source,
        re.S,
    )
    if not match:
        return None
    return re.findall(r'"([a-z_]+)"', match.group(1))


def markdown_files() -> list[Path]:
    return sorted(p for p in KB_ROOT.rglob("*.md") if "tools" not in p.parts)


def main() -> int:
    worst = PASS
    def note(status: str, check: str, detail: str) -> None:
        nonlocal worst
        emit(status, check, detail)
        rank = {PASS: 0, WARN: 1, FAIL: 2}
        if rank[status] > rank[worst]:
            worst = status

    manifest_path = KB_ROOT / "manifest.json"
    readme_path = KB_ROOT / "README.md"
    manifest = json.loads(read_text(manifest_path))
    readme = read_text(readme_path)

    version = str(manifest.get("version", ""))
    revision = str(manifest.get("project_revision", ""))
    if re.search(rf"知识库版本为 `{re.escape(version)}`", readme):
        note(PASS, "readme_version", f"README and manifest version={version}")
    else:
        note(FAIL, "readme_version", f"README missing version {version}")
    if revision and revision in readme:
        note(PASS, "readme_revision", f"README contains project_revision {revision[:12]}")
    else:
        note(FAIL, "readme_revision", "README project revision does not match manifest")

    missing_paths = []
    for doc in manifest.get("documents", []):
        path = KB_ROOT / doc["path"]
        if not path.is_file():
            missing_paths.append(doc["path"])
        for field in ("information_type", "status", "code_revision"):
            if field not in doc:
                note(WARN, "manifest_metadata", f"{doc.get('id')} missing {field}")
    if missing_paths:
        note(FAIL, "manifest_paths", f"missing files: {missing_paths}")
    else:
        note(PASS, "manifest_paths", f"{len(manifest.get('documents', []))} document paths exist")

    registered = {doc["path"] for doc in manifest.get("documents", [])}
    orphan = []
    for md in markdown_files():
        rel = md.relative_to(KB_ROOT).as_posix()
        if md.name in REPORT_NAMES:
            continue
        if rel not in registered:
            orphan.append(rel)
    if orphan:
        note(FAIL, "manifest_coverage", f"markdown not in manifest: {orphan}")
    else:
        note(PASS, "manifest_coverage", "all non-report markdown files are registered")

    extra_in_manifest = sorted(registered - {p.relative_to(KB_ROOT).as_posix() for p in markdown_files()})
    if extra_in_manifest:
        note(FAIL, "manifest_extra", f"manifest paths not found as markdown: {extra_in_manifest}")

    registry_path = REPO_ROOT / "traffic_control" / "registry.py"
    modes = control_modes_from_registry(registry_path)
    expected = ["fixed", "sotl", "max_pressure", "ippo", "mappo"]
    if sorted(modes) != sorted(expected):
        note(FAIL, "control_modes_code", f"registry={modes} expected={expected}")
    else:
        note(PASS, "control_modes_code", f"CONTROL_MODE_REGISTRY={modes}")
    cap = read_text(KB_ROOT / "07_project" / "supported_control_capabilities.md")
    missing_mode_docs = [m for m in modes if f"`{m}`" not in cap]
    if missing_mode_docs:
        note(FAIL, "control_modes_kb", f"capabilities doc missing {missing_mode_docs}")
    else:
        note(PASS, "control_modes_kb", "supported_control_capabilities lists all registry modes")

    presets_path = REPO_ROOT / "backend" / "app" / "scenario" / "presets.py"
    presets = presets_from_py(presets_path)
    expected_presets = {
        "xiongan_20": {"label": "雄安20路口路网", "n": 20},
        "east_dense": {"label": "校园周边场景", "ids": ["demo_3", "demo_5", "demo_6", "demo_9"]},
        "west_dense": {"label": "窄路密网片区场景", "ids": ["demo_14", "demo_15", "demo_19"]},
    }
    scenario_doc = read_text(KB_ROOT / "07_project" / "scenario_definition.md")
    preset_ok = True
    for pid, spec in expected_presets.items():
        actual = presets.get(pid)
        if actual is None:
            note(FAIL, "presets_code", f"missing preset {pid}")
            preset_ok = False
            continue
        if actual.get("label") != spec["label"]:
            note(FAIL, "presets_code", f"{pid} label={actual.get('label')!r} expected={spec['label']!r}")
            preset_ok = False
        if "ids" in spec and list(actual.get("intersection_ids") or []) != spec["ids"]:
            note(FAIL, "presets_code", f"{pid} intersections={actual.get('intersection_ids')}")
            preset_ok = False
        if pid not in scenario_doc or spec["label"] not in scenario_doc:
            note(FAIL, "presets_kb", f"scenario_definition missing {pid} or label {spec['label']}")
            preset_ok = False
    if preset_ok:
        note(PASS, "presets", "preset id/label/intersections match code and scenario_definition")

    events_path = REPO_ROOT / "simulation" / "sumo" / "engine" / "session.py"
    events = event_types_from_session(events_path) or []
    expected_events = [
        "lane_closure",
        "speed_limit",
        "accident",
        "major_event_opening",
        "major_event_closing",
    ]
    if events != expected_events:
        note(FAIL, "event_types_code", f"session event_types={events}")
    else:
        note(PASS, "event_types_code", f"event types={events}")
    cases = read_text(KB_ROOT / "04_scenarios" / "ai_control_cases.md")
    missing_events = [e for e in expected_events if f"`{e}`" not in scenario_doc or f"`{e}`" not in cases]
    if missing_events:
        note(FAIL, "event_types_kb", f"scenario or cases missing {missing_events}")
    else:
        note(PASS, "event_types_kb", "scenario_definition and ai_control_cases cover all event types")

    stale_hits = []
    for md in markdown_files():
        if md.name in REPORT_NAMES:
            continue
        text = read_text(md)
        for phrase in STALE_PHRASES:
            if phrase in text:
                stale_hits.append(f"{md.relative_to(KB_ROOT)}:{phrase}")
    if stale_hits:
        note(FAIL, "stale_phrases", "; ".join(stale_hits))
    else:
        note(PASS, "stale_phrases", "no stale positioning phrases in RAG markdown")

    boundaries = read_text(KB_ROOT / "07_project" / "llm_decision_boundaries.md")
    architecture = read_text(KB_ROOT / "07_project" / "ai_control_architecture.md")
    schema = read_text(KB_ROOT / "07_project" / "ai_control_output_schema.md")
    executor = read_text(KB_ROOT / "07_project" / "ai_plan_executor.md")
    required_bits = {
        "boundaries_can_control": "临时" in boundaries and "target_phase" in boundaries,
        "boundaries_no_algorithm_choice": "替用户选择 baseline controller" in boundaries,
        "architecture_not_control_mode": "不是" in architecture and "control_mode" in architecture,
        "schema_not_lock_phase": "不能" in schema and "持续保持" in schema,
        "executor_dual_timescale": "decision_interval" in executor and "30–60" in executor,
        "atomic_fallback": "原子计划" in schema and "整体 fallback" in boundaries,
    }
    missing_bits = [name for name, ok in required_bits.items() if not ok]
    if missing_bits:
        note(FAIL, "ai_positioning", f"missing consistent statements: {missing_bits}")
    else:
        note(PASS, "ai_positioning", "architecture / boundaries / schema / executor agree on Qwen role")

    conflict_needles = [
        "只生成结构化建议",
        "LLM只用于算法选择",
        "策略推荐必须限定为 control_mode registry",
        "20个节点STGCN",
        "20 个节点 STGCN",
    ]
    negation = ("不是", "不得写成", "不要写成", "不能写成")
    conflict_hits = []
    for md in markdown_files():
        if md.name in REPORT_NAMES:
            continue
        for lineno, line in enumerate(read_text(md).splitlines(), 1):
            for needle in conflict_needles:
                if needle not in line:
                    continue
                if any(token in line for token in negation):
                    continue
                conflict_hits.append(f"{md.relative_to(KB_ROOT)}:{lineno}:{needle}")
    if conflict_hits:
        note(FAIL, "conflict_phrases", "; ".join(conflict_hits))
    else:
        note(PASS, "conflict_phrases", "no architecture/boundary conflict phrases")

    print(f"RESULT\t{worst}")
    return 0 if worst == PASS else 1 if worst == FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
