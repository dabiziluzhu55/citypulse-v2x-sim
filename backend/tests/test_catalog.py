"""仿真目录接口测试"""

from fastapi.testclient import TestClient


def test_catalog_returns_demo_2(client: TestClient) -> None:
    response = client.get("/api/v1/catalog")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["intersections"]) == 1
    assert payload["intersections"][0]["intersection_id"] == "demo_2"
    assert payload["control_modes"] == ["fixed", "max_pressure"]
    assert payload["flow_multiplier"]["min"] == 0.1
    assert payload["flow_multiplier"]["max"] == 5.0


def test_catalog_returns_503_when_artifacts_missing(degraded_client: TestClient) -> None:
    response = degraded_client.get("/api/v1/catalog")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ARTIFACTS_NOT_READY"
