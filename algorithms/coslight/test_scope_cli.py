import pytest

from algorithms.coslight.scope_cli import (
    build_scope_block, parse_intersections, resolve_scope,
)
from config.scenario_presets import ResolvedScenarioScope


def test_parse_single_integer_expands_demo_range():
    assert parse_intersections("3") == ("demo_1", "demo_2", "demo_3")
    assert parse_intersections("20") == tuple(f"demo_{i}" for i in range(1, 21))


def test_parse_comma_list_strips_and_preserves_order():
    assert parse_intersections("demo_3, demo_5 ,demo_6,demo_9") == (
        "demo_3", "demo_5", "demo_6", "demo_9")


@pytest.mark.parametrize("value", [
    "", "demo_1,,demo_2", "demo_1,demo_1", "foo", "demo_1,bar",
    "0", "21", "demo_1;demo_2",
])
def test_parse_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        parse_intersections(value)


def test_resolve_scope_preset_custom_default():
    preset = resolve_scope("east_dense", None)
    assert preset.source == "preset"
    assert preset.preset_id == "east_dense"
    assert preset.managed_ids == ("demo_3", "demo_5", "demo_6", "demo_9")
    custom = resolve_scope(None, ("demo_1", "demo_2"))
    assert custom.source == "custom"
    assert custom.managed_ids == ("demo_1", "demo_2")
    default = resolve_scope(None, None)
    assert default.source == "default"
    assert len(default.managed_ids) == 20


def test_resolve_scope_unknown_preset_raises():
    with pytest.raises(ValueError, match="east_dense2"):
        resolve_scope("east_dense2", None)


def test_build_scope_block_matches_stats_scope_block():
    scope = resolve_scope("east_dense", None)
    block = build_scope_block(scope, tuple(f"demo_{i}" for i in range(1, 21)))
    assert block == {
        "source": "preset", "preset_id": "east_dense",
        "registered_intersections": 20,
        "algorithm_controlled_intersections": 4,
        "fixed_intersections": 16,
        "managed_ids": ["demo_3", "demo_5", "demo_6", "demo_9"],
    }
    from algorithms.v2x.collab.stats import scope_block as stats_scope_block
    assert block == stats_scope_block(scope, tuple(f"demo_{i}" for i in range(1, 21)))