"""地图瓦片代理接口"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Request, Response

from ...core.config import Settings, get_settings
from ...core.exceptions import AppError

router = APIRouter()

VALID_TIANDITU_LAYERS = frozenset({"img", "cia"})
TIANDITU_SUBDOMAINS = ("0", "1", "2", "3", "4", "5", "6", "7")


def _pick_subdomain(request: Request) -> str:
    tile_col = request.query_params.get("tileCol", "0")
    try:
        index = int(tile_col) % len(TIANDITU_SUBDOMAINS)
    except ValueError:
        index = 0
    return TIANDITU_SUBDOMAINS[index]


@router.get("/tiles/tianditu/{layer}/wmts")
async def proxy_tianditu_wmts(
    layer: str,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Response:
    if layer not in VALID_TIANDITU_LAYERS:
        raise AppError(
            code="UNSUPPORTED_TILE_LAYER",
            message=f"Unsupported Tianditu layer {layer!r}. Expected one of: img, cia.",
            status_code=422,
        )

    token = (settings.tianditu_token or "").strip()
    if not token:
        raise AppError(
            code="TIANDITU_TOKEN_UNAVAILABLE",
            message="Tianditu token is not configured on the backend.",
            status_code=503,
        )

    subdomain = _pick_subdomain(request)
    upstream_url = f"https://t{subdomain}.tianditu.gov.cn/{layer}_w/wmts"
    params = dict(request.query_params)
    params["tk"] = token

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            upstream_response = await client.get(upstream_url, params=params)
    except httpx.HTTPError as exc:
        raise AppError(
            code="TIANDITU_UPSTREAM_ERROR",
            message=f"Failed to fetch Tianditu tile: {exc}",
            status_code=502,
        ) from exc

    headers: dict[str, str] = {}
    content_type = upstream_response.headers.get("content-type")
    if content_type:
        headers["Content-Type"] = content_type
    if upstream_response.is_success:
        headers["Cache-Control"] = "public, max-age=3600"

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=headers,
    )
