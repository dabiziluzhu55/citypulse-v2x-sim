"""健康检查接口测试"""

from fastapi.testclient import TestClient


def test_health_ok(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["generated_artifacts_ready"] is True
    assert payload["simulation_manager_ready"] is True


def test_health_degraded(degraded_client: TestClient) -> None:
    response = degraded_client.get("/api/v1/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["generated_artifacts_ready"] is False
    assert "missing_files" in payload
