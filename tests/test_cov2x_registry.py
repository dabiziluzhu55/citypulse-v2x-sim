from traffic_control.registry import require_control_mode


def test_cov2x_is_registered_as_local_protocol_algorithm() -> None:
    spec = require_control_mode("cov2x")

    assert spec.kernel_mode == "algorithm"
    assert spec.algorithm_transport == "local"
    assert spec.algorithm_module == "traffic_control.cov2x"
    assert spec.supported_presets == ("xiongan_20", "east_dense", "west_dense")
