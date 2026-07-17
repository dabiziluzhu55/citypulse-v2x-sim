"""内部管控算法协议端点：仅供 SUMO SimulationManager HTTP 回调，不对前端暴露业务语义。

路径：/api/v1/internal/algorithm/{algorithm_name}/initialize|step|finish
协议对齐 docs/algorithm_interface.md（protocol 2.0）；算法差异在 controllers 层实现。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from ...controllers.runtime import SUPPORTED_ALGORITHMS, AlgorithmRuntimeStore

router = APIRouter(prefix="/internal/algorithm")
logger = logging.getLogger(__name__)


def get_algorithm_store(request: Request) -> AlgorithmRuntimeStore:
    return request.app.state.algorithm_store


def _ensure_algorithm(algorithm_name: str) -> str:
    if algorithm_name not in SUPPORTED_ALGORITHMS:
        raise HTTPException(status_code=404, detail=f"Unknown algorithm: {algorithm_name}")
    return algorithm_name


@router.post("/{algorithm_name}/initialize")
def initialize(
    algorithm_name: str,
    body: dict,
    store: AlgorithmRuntimeStore = Depends(get_algorithm_store),
) -> dict:
    name = _ensure_algorithm(algorithm_name)
    try:
        return store.initialize(name, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("algorithm initialize failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{algorithm_name}/step")
def step(
    algorithm_name: str,
    body: dict,
    store: AlgorithmRuntimeStore = Depends(get_algorithm_store),
) -> dict:
    name = _ensure_algorithm(algorithm_name)
    try:
        return store.step(name, body)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("algorithm step failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{algorithm_name}/finish")
def finish(
    algorithm_name: str,
    body: dict,
    store: AlgorithmRuntimeStore = Depends(get_algorithm_store),
) -> dict:
    name = _ensure_algorithm(algorithm_name)
    try:
        return store.finish(name, body)
    except Exception as exc:
        logger.exception("algorithm finish failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
