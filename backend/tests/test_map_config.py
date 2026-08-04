"""地图配置与瓦片代理测试"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings, get_settings


@pytest.fixture
def map_token_settings() -> Settings:
    settings = get_settings()
    settings.cesium_ion_token = "cesium-test-token"
    settings.tianditu_token = "tianditu-test-token"
    return settings


@pytest.fixture
def map_config_client(client: TestClient, map_token_settings: Settings) -> TestClient:
    client.app.dependency_overrides[get_settings] = lambda: map_token_settings
    yield client
    client.app.dependency_overrides.clear()


def test_map_config_disabled(client: TestClient) -> None:
    settings = get_settings()
    settings.cesium_ion_token = None
    settings.tianditu_token = None
    client.app.dependency_overrides[get_settings] = lambda: settings

    response = client.get("/api/v1/config/map")
    assert response.status_code == 200
    payload = response.json()
    assert payload["cesium_ion_token"] is None
    assert payload["tianditu_enabled"] is False

    client.app.dependency_overrides.clear()


def test_map_config_with_tokens(map_config_client: TestClient) -> None:
    response = map_config_client.get("/api/v1/config/map")
    assert response.status_code == 200
    payload = response.json()
    assert payload["cesium_ion_token"] == "cesium-test-token"
    assert payload["tianditu_enabled"] is True


def test_tianditu_proxy_requires_token(client: TestClient) -> None:
    settings = get_settings()
    settings.tianditu_token = None
    client.app.dependency_overrides[get_settings] = lambda: settings

    response = client.get(
        "/api/v1/tiles/tianditu/img/wmts",
        params={
            "service": "WMTS",
            "request": "GetTile",
            "version": "1.0.0",
            "layer": "img",
            "style": "default",
            "format": "tiles",
            "tileMatrixSet": "w",
            "tileMatrix": "10",
            "tileRow": "1",
            "tileCol": "2",
        },
    )
    assert response.status_code == 503

    client.app.dependency_overrides.clear()


def test_tianditu_proxy_rejects_unknown_layer(map_config_client: TestClient) -> None:
    response = map_config_client.get("/api/v1/tiles/tianditu/unknown/wmts")
    assert response.status_code == 422


def test_tianditu_proxy_forwards_request(map_config_client: TestClient) -> None:
    upstream_response = httpx.Response(
        status_code=200,
        content=b"tile-bytes",
        headers={"content-type": "image/png"},
        request=httpx.Request("GET", "https://t0.tianditu.gov.cn/img_w/wmts"),
    )
    mock_get = AsyncMock(return_value=upstream_response)
    mock_client = AsyncMock()
    mock_client.get = mock_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.app.api.v1.tiles.httpx.AsyncClient", return_value=mock_client):
        response = map_config_client.get(
            "/api/v1/tiles/tianditu/img/wmts",
            params={
                "service": "WMTS",
                "request": "GetTile",
                "version": "1.0.0",
                "layer": "img",
                "style": "default",
                "format": "tiles",
                "tileMatrixSet": "w",
                "tileMatrix": "10",
                "tileRow": "1",
                "tileCol": "2",
            },
        )

    assert response.status_code == 200
    assert response.content == b"tile-bytes"
    assert response.headers["content-type"] == "image/png"
    mock_get.assert_awaited_once()
    called_url = mock_get.await_args.args[0]
    called_params = mock_get.await_args.kwargs["params"]
    assert called_url.startswith("https://t")
    assert called_url.endswith("/img_w/wmts")
    assert called_params["tk"] == "tianditu-test-token"
    assert called_params["tileCol"] == "2"
