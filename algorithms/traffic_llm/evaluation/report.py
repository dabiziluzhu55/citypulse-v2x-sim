"""Write closed-loop efficacy JSON/Markdown reports."""

from __future__ import annotations

from typing import Any, Mapping


PRIMARY_LABELS = (
    ("local_avg_queue_veh", "local_avg_queue"),
    ("local_max_queue_m", "local_max_queue"),
    ("local_spillback_pct", "local_spillback"),
    ("local_mean_speed_mps", "local_mean_speed"),
    ("local_throughput_delta", "local_throughput"),
    ("recovery_time_s", "recovery_time"),
)


def _pct(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):+.1f}%"


def _wtl(payload: Mapping[str, Any] | None) -> str:
    if not payload:
        return "n/a"
    wtl = payload.get("win_tie_loss") or {}
    return f"win {wtl.get('win', 0)} / tie {wtl.get('tie', 0)} / loss {wtl.get('loss', 0)} (n={wtl.get('n', 0)})"


def _rate(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{100.0 * float(value):.1f}%"


def _ms(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.1f}"


QUEUE_SPILLBACK = (
    "local_avg_queue_veh",
    "local_max_queue_m",
    "local_spillback_pct",
)


def decide_next_step(report: Mapping[str, Any]) -> dict[str, Any]:
    json_ok = float(report.get("json_ok_rate") or 0.0)
    schema_ok = float(report.get("schema_ok_rate") or 0.0)
    phase_ok = float(report.get("phase_ok_rate") or 0.0)
    region_ok = float(report.get("region_ok_rate") or 0.0)
    overall = dict(report.get("overall") or {})
    vs_base = (overall.get("base_qwen") or {}).get("win_tie_loss") or {}
    vs_fixed = (overall.get("fixed") or {}).get("win_tie_loss") or {}
    vs_v1 = (overall.get("traffic_qwen_v1") or {}).get("win_tie_loss") or {}
    vs_mp = (overall.get("max_pressure") or {}).get("win_tie_loss") or {}
    legality_ok = min(json_ok, schema_ok, phase_ok, region_ok) >= 0.98
    beat_fixed = int(vs_fixed.get("win") or 0) > int(vs_fixed.get("loss") or 0)
    beat_base = int(vs_base.get("win") or 0) > int(vs_base.get("loss") or 0)
    beat_v1 = int(vs_v1.get("win") or 0) > int(vs_v1.get("loss") or 0)
    competitive_mp = int(vs_mp.get("win") or 0) >= int(vs_mp.get("loss") or 0)
    means = dict((overall.get("fixed") or {}).get("mean_improvement_pct") or {})
    queue_vals = [means.get(key) for key in QUEUE_SPILLBACK]
    present_queue = [float(item) for item in queue_vals if item is not None]
    queue_improved = bool(present_queue) and (sum(present_queue) / len(present_queue) > 0.0)
    by_event = dict(report.get("by_event") or {})
    event_net = []
    for event, payload in by_event.items():
        wtl = ((payload.get("fixed") or {}).get("win_tie_loss") or {})
        wins = int(wtl.get("win") or 0)
        losses = int(wtl.get("loss") or 0)
        if int(wtl.get("n") or 0) <= 0:
            continue
        event_net.append((event, wins > losses, wins, losses))
    n_event_ok = sum(1 for item in event_net if item[1])
    majority_events = n_event_ok >= 3 if len(event_net) >= 5 else (
        n_event_ok > (len(event_net) - n_event_ok) if event_net else False
    )
    weak_events = [item[0] for item in event_net if not item[1]]
    deploy = bool(legality_ok and beat_fixed and queue_improved and majority_events)
    return {
        "deployment_ready": deploy,
        "recommend_awq_vllm_backend": False,
        "legality_ok": legality_ok,
        "improves_vs_base": beat_base,
        "improves_vs_fixed": beat_fixed,
        "improves_vs_v1": beat_v1,
        "queue_spillback_improved": queue_improved,
        "majority_events_improved_vs_fixed": majority_events,
        "n_events_improved_vs_fixed": n_event_ok,
        "n_events_compared": len(event_net),
        "weak_events_vs_fixed": weak_events,
        "competitive_vs_max_pressure": competitive_mp,
        "data_focus": [{"kind": "event", "name": name} for name in weak_events],
        "reason": (
            "Traffic-Qwen 已证明能够在未见扰动场景中相对固定配时改善交通运行，可以进入轻量化部署阶段。"
            if deploy
            else "相对 Fixed 的闭环改善尚未达到进入轻量化部署的门槛；本轮不训练、不部署。"
        ),
    }


def _slice_block(title: str, grouped: Mapping[str, Any]) -> list[str]:
    lines = [f"## {title}", ""]
    for name, payload in grouped.items():
        lines.append(f"### {name}")
        for other in ("fixed", "base_qwen", "traffic_qwen_v1", "max_pressure"):
            block = dict(payload.get(other) or {})
            means = dict(block.get("mean_improvement_pct") or {})
            lines.append(f"- vs {other}: `{_wtl(block)}`")
            for metric_id, label in PRIMARY_LABELS:
                lines.append(f"  - {label}: `{_pct(means.get(metric_id))}`")
        lines.append("")
    return lines


def render_markdown(report: Mapping[str, Any]) -> str:
    decision = dict(report.get("decision") or decide_next_step(report))
    overall = dict(report.get("overall") or {})
    latency = dict(report.get("inference_latency_ms") or {})
    lines = [
        "# Traffic-Qwen 闭环评测",
        "",
        "离线 policy efficacy：计划直接写入进程内 SUMO。这不是实时部署。",
        "主结论看 local event-window + recovery；xiongan_20 的不可靠 TripInfo 不参与结论。",
        "AI 计划仅在扰动 ACTIVE 期间生效（120–180s 事件可执行 [120, 150] 两段 30s）。",
        "",
        f"- Traffic-Qwen V2 完成：`{report.get('n_llm_completed')}/{report.get('n_scenarios')}`",
        f"- Base / V1 / Fixed / MP 完成：`{report.get('n_base_completed')}` / `{report.get('n_v1_completed')}` / `{report.get('n_fixed_completed')}` / `{report.get('n_max_pressure_completed')}`",
        f"- 无效扰动（未 ACTIVE，已剔除出效果比较）：`{report.get('n_invalid_event')}`",
        f"- JSON / schema / phase / region：`{_rate(report.get('json_ok_rate'))}` / `{_rate(report.get('schema_ok_rate'))}` / `{_rate(report.get('phase_ok_rate'))}` / `{_rate(report.get('region_ok_rate'))}`",
        f"- fallback rate：`{_rate(report.get('fallback_rate'))}`",
        f"- invalid_plan rate：`{_rate(report.get('invalid_plan_rate'))}`",
        f"- 推理延迟 P50 / P95 / max ms：`{_ms(latency.get('p50'))}` / `{_ms(latency.get('p95'))}` / `{_ms(latency.get('max'))}`",
        "",
        "## 综合结论",
        "",
        f"1. **V2 vs Fixed（主判定）**：`{_wtl(overall.get('fixed'))}`；W>L：`{decision.get('improves_vs_fixed')}`；queue/spillback 改善：`{decision.get('queue_spillback_improved')}`",
        f"2. 五类 event 净改善：`{decision.get('n_events_improved_vs_fixed')}/{decision.get('n_events_compared')}`；较弱事件：`{decision.get('weak_events_vs_fixed')}`",
        f"3. 补充 V2 vs Base：`{_wtl(overall.get('base_qwen'))}`；V2 vs V1：`{_wtl(overall.get('traffic_qwen_v1'))}`",
        f"4. 补充 V2 vs Max Pressure：`{_wtl(overall.get('max_pressure'))}`（不是部署硬门槛）",
        f"5. 推理 P50/P95：`{_ms(latency.get('p50'))}` / `{_ms(latency.get('p95'))}` ms",
        f"6. `deployment_ready={decision.get('deployment_ready')}` — {decision.get('reason')}",
        "7. 本轮不自动 merge / AWQ / vLLM / Backend / Frontend / Docker。",
        "",
        "## Overall 相对改善（Traffic-Qwen 相对对照；+ 更好）",
        "",
    ]
    for name in ("fixed", "base_qwen", "traffic_qwen_v1", "max_pressure"):
        block = overall.get(name) or {}
        means = dict(block.get("mean_improvement_pct") or {})
        lines.append(f"### {name}")
        lines.append(f"- scenario W/T/L: `{_wtl(block)}`")
        for metric_id, label in PRIMARY_LABELS:
            lines.append(f"- {label}: `{_pct(means.get(metric_id))}`")
        lines.append("")
    lines.extend(_slice_block("按 event type", dict(report.get("by_event") or {})))
    lines.extend(_slice_block("按 scope", dict(report.get("by_scope") or {})))
    lines.extend(_slice_block("按 period", dict(report.get("by_period") or {})))
    lines.append("xiongan_20 TripInfo 继续 gate；结论使用 local event-window + recovery。")
    return "\n".join(lines) + "\n"
