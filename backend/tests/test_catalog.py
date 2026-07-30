"""仿真目录接口测试"""

from fastapi.testclient import TestClient


def test_catalog_returns_scenario_presets(client: TestClient) -> None:
    response = client.get("/api/v1/catalog")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["intersections"]) == 20
    intersection_ids = {item["intersection_id"] for item in payload["intersections"]}
    assert intersection_ids == {f"demo_{index}" for index in range(1, 21)}
    assert payload["control_modes"] == ["fixed", "max_pressure", "sotl"]
    assert payload["playback_speeds"] == [1.0, 1.25, 1.5, 2.0, 3.0, 5.0]

    presets = payload["scenario_presets"]
    assert [item["preset_id"] for item in presets] == [
        "demo_2_single",
        "east_dense",
        "west_dense",
        "xiongan_20",
    ]
    demo_2_single = next(item for item in presets if item["preset_id"] == "demo_2_single")
    assert demo_2_single["intersection_ids"] == ["demo_2"]
    assert demo_2_single["map_template"] == "xiongan20"
    east_dense = next(item for item in presets if item["preset_id"] == "east_dense")
    assert east_dense["intersection_ids"] == ["demo_3", "demo_5", "demo_6", "demo_9"]
    assert east_dense["map_template"] == "east_dense"
    west_dense = next(item for item in presets if item["preset_id"] == "west_dense")
    assert west_dense["intersection_ids"] == ["demo_14", "demo_15", "demo_19"]


def test_catalog_returns_503_when_artifacts_missing(degraded_client: TestClient) -> None:
    response = degraded_client.get("/api/v1/catalog")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ARTIFACTS_NOT_READY"
