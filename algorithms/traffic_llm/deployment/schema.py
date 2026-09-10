"""JSON schema for Traffic-Qwen 30s AIControlPlan structured decoding.

Backend / SUMO still MUST validate with AIControlPlan.from_mapping.
Structured output is a generation constraint, not a safety check.
"""

from __future__ import annotations

from typing import Any


PLAN_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "controlled_intersections": {
            "type": "array",
            "items": {"type": "string"},
        },
        "valid_seconds": {"type": "number"},
        "signal_plan": {
            "type": "object",
            "additionalProperties": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 6,
                "maxItems": 6,
            },
        },
        "objective": {"type": "string"},
        "reason": {"type": "string"},
        "fallback_to_baseline": {"type": "boolean"},
    },
    "required": [
        "controlled_intersections",
        "valid_seconds",
        "signal_plan",
        "objective",
        "reason",
        "fallback_to_baseline",
    ],
    "additionalProperties": False,
}


OPENAI_JSON_SCHEMA_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "aicontrolplan",
        "schema": PLAN_JSON_SCHEMA,
        "strict": False,
    },
}
