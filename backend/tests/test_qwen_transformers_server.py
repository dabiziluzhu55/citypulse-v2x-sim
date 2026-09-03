"""Tests for the lightweight Qwen service protocol adapter."""

from __future__ import annotations

import json

from scripts.llm.qwen_transformers_server import _tool_call_from_text


def _arguments(call: dict) -> dict:
    return json.loads(call["function"]["arguments"])


def test_parses_tagged_and_bare_json_tool_calls() -> None:
    allowed = {"get_current_traffic"}
    tagged = _tool_call_from_text(
        '<tool_call>{"name":"get_current_traffic","arguments":{}}</tool_call>',
        allowed,
    )
    bare = _tool_call_from_text(
        '```json\n{"name":"get_current_traffic","arguments":{"intersection_id":"demo_2"}}\n```',
        allowed,
    )

    assert tagged is not None
    assert tagged["function"]["name"] == "get_current_traffic"
    assert _arguments(tagged) == {}
    assert bare is not None
    assert _arguments(bare) == {"intersection_id": "demo_2"}


def test_does_not_treat_control_plan_or_unknown_name_as_tool_call() -> None:
    control_plan = '{"controlled_intersections":["demo_2"],"signal_plan":{}}'
    unknown = '{"name":"set_signal_phase","arguments":{}}'

    assert _tool_call_from_text(control_plan, set()) is None
    assert _tool_call_from_text(unknown, {"get_current_traffic"}) is None
