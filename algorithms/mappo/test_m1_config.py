from algorithms.mappo.m1_config import M1Config, validate_m1_config


def test_default_weights_and_validation():
    cfg = M1Config(
        arm="m1_a",
        local_weight=0.95,
        neighbor_weight=0.0,
        team_weight=0.05,
        adjacency_path="algorithms/mappo/runs/mappo_v2/m0/"
        "intersection_adjacency_m1_symmetric.json",
    )
    validate_m1_config(cfg)  # 不抛异常


def test_invalid_weight_sum_rejected():
    import pytest
    cfg = M1Config(arm="m1_b", local_weight=0.5, neighbor_weight=0.6, team_weight=0.1)
    with pytest.raises(ValueError):
        validate_m1_config(cfg)


def test_m1_a_neighbor_weight_must_be_zero():
    import pytest
    cfg = M1Config(arm="m1_a", local_weight=0.9, neighbor_weight=0.05, team_weight=0.05)
    with pytest.raises(ValueError):
        validate_m1_config(cfg)
