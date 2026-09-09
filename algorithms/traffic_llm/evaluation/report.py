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


def decide_next_step(report: Mapping[str, Any]) -> dict[str, Any]:
    json_ok = float(report.get("json_ok_rate") or 0.0)
    schema_ok = float(report.get("schema_ok_rate") or 0.0)
    phase_ok = float(report.get("phase_ok_rate") or 0.0)
    overall = dict(report.get("overall") or {})
    vs_base = (overall.get("base_qwen") or {}).get("win_tie_loss") or {}
    vs_fixed = (overall.get("fixed") or {}).get("win_tie_loss") or {}
    vs_mp = (overall.get("max_pressure") or {}).get("win_tie_loss") or {}
    legality_ok = min(json_ok, schema_ok, phase_ok) >= 0.98
    n_base = max(1, int(vs_base.get("n") or 0))
    n_fixed = max(1, int(vs_fixed.get("n") or 0))
    n_mp = max(1, int(vs_mp.get("n") or 0))
    beat_base = (int(vs_base.get("win") or 0) + 0.5 * int(vs_base.get("tie") or 0)) / n_base >= 0.55
    beat_fixed = (int(vs_fixed.get("win") or 0) + 0.5 * int(vs_fixed.get("tie") or 0)) / n_fixed >= 0.55
    competitive_mp = (int(vs_mp.get("loss") or 0) / n_mp) <= 0.50
    deploy = bool(legality_ok and beat_base and beat_fixed and competitive_mp)
    weakest = []
    by_event = dict(report.get("by_event") or {})
    for event, payload in by_event.items():
        mp = ((payload.get("max_pressure") or {}).get("win_tie_loss") or {})
        n = max(1, int(mp.get("n") or 0))
        loss_rate = int(mp.get("loss") or 0) / n
        weakest.append((loss_rate, event, "event"))
    by_scope = dict(report.get("by_scope") or {})
    for scope, payload in by_scope.items():
        mp = ((payload.get("max_pressure") or {}).get("win_tie_loss") or {})
        n = max(1, int(mp.get("n") or 0))
        loss_rate = int(mp.get("loss") or 0) / n
        weakest.append((loss_rate, scope, "scope"))
    weakest.sort(reverse=True)
    data_focus = []
    for loss_rate, name, kind in weakest[:3]:
        if loss_rate >= 0.45:
            data_focus.append({"kind": kind, "name": name, "mp_loss_rate": loss_rate})
    return {
        "recommend_awq_vllm_backend": deploy,
        "legality_ok": legality_ok,
        "improves_vs_base": beat_base,
        "improves_vs_fixed": beat_fixed,
        "competitive_vs_max_pressure": competitive_mp,
        "data_focus": data_focus,
        "reason": (
            "合法率接近 100%，相对 Base/Fixed 在主扰动指标上稳定改善，且多数 scenario 不明显劣于 Max Pressure。"
            if deploy
            else "闭环交通效果尚未达到部署门槛；不要 merge / AWQ / vLLM / Backend takeover。"
        ),
    }


def _slice_block(title: str, grouped: Mapping[str, Any]) -> list[str]:
    lines = [f"## {title}", ""]
    for name, payload in grouped.items():
        lines.append(f"### {name}")
        for other in ("base_qwen", "fixed", "max_pressure", "selected_expert"):
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
        f"- Traffic-Qwen 完成：`{report.get('n_llm_completed')}/{report.get('n_scenarios')}`",
        f"- Base Qwen 完成：`{report.get('n_base_completed')}/{report.get('n_scenarios')}`",
        f"- 扰动事件未 ACTIVE、因而无法接管的 episode：`{report.get('n_zero_plan_episodes')}`（SUMO accident spawn 失败，原 formal_v1 六算法同样无 ACTIVE 事故）",
        f"- JSON / schema / phase / region：`{_rate(report.get('json_ok_rate'))}` / `{_rate(report.get('schema_ok_rate'))}` / `{_rate(report.get('phase_ok_rate'))}` / `{_rate(report.get('region_ok_rate'))}`",
        f"- fallback rate：`{_rate(report.get('fallback_rate'))}`",
        f"- invalid_plan rate：`{_rate(report.get('invalid_plan_rate'))}`",
        f"- 推理延迟 P50 / P95 / max ms：`{_ms(latency.get('p50'))}` / `{_ms(latency.get('p95'))}` / `{_ms(latency.get('max'))}`",
        "",
        "## 综合结论",
        "",
        f"1. 相比 Base Qwen：`{_wtl(overall.get('base_qwen'))}`；判定改善：`{decision.get('improves_vs_base')}`",
        f"2. 相比 Fixed：`{_wtl(overall.get('fixed'))}`；判定改善：`{decision.get('improves_vs_fixed')}`",
        f"3. 相比 Max Pressure：`{_wtl(overall.get('max_pressure'))}`；判定有竞争力：`{decision.get('competitive_vs_max_pressure')}`",
        f"4. 相比 selected expert：`{_wtl(overall.get('selected_expert'))}`",
        f"5. 最弱切片：`{decision.get('data_focus')}`",
        f"6. 推理 P50/P95：`{_ms(latency.get('p50'))}` / `{_ms(latency.get('p95'))}` ms",
        f"7. 是否进入 AWQ + vLLM + Backend 异步 takeover：**{'否' if not decision.get('recommend_awq_vllm_backend') else '是'}** — {decision.get('reason')}",
        "",
        "## Overall 相对改善（Traffic-Qwen 相对对照；+ 更好）",
        "",
    ]
    for name in ("base_qwen", "fixed", "max_pressure", "selected_expert"):
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
