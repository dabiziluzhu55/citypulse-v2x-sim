from algorithms.mappo.preregistration import build_manifest_yaml, validate_manifest


def test_manifest_contains_required_keys():
    yaml_text = build_manifest_yaml(
        ippo_baseline_sha256="a" * 64,
        adjacency_sha256={"directed": "b" * 64, "symmetric": "c" * 64},
        shared_init_sha256="d" * 64,
        net_xml_sha256="e" * 64,
        vanilla_ckpt_sha256="f" * 64,
        ippo_ckpt_sha256="f" * 64,
        arrival_delta_min=2.0,
        ep32_gate={"waiting_delta_vs_m10_mean_max": 0.0},
    )
    for key in ("statistical_tests", "seeds:", "arms:", "ep32_gate:"):
        assert key in yaml_text
    assert validate_manifest(yaml_text) is None


def test_manifest_rejects_missing_sha():
    import pytest
    yaml_text = build_manifest_yaml(
        ippo_baseline_sha256="a" * 64, adjacency_sha256={}, shared_init_sha256="d" * 64,
        net_xml_sha256="e" * 64, vanilla_ckpt_sha256="f" * 64, ippo_ckpt_sha256="g" * 64,
        arrival_delta_min=2.0, ep32_gate={},
    )
    with pytest.raises(ValueError):
        validate_manifest(yaml_text)
