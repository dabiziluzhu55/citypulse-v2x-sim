"""Traffic Copilot 对话 API。"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import APIRouter, Depends, Request

from ...copilot.llm import (
    LLMError,
    LLMInputError,
    LLMProtocolError,
    LLMProvider,
    LLMUnavailableError,
)
from ...copilot.orchestrator import (
    CopilotError,
    CopilotInputError,
    CopilotLimitError,
    CopilotModelError,
    CopilotOrchestrator,
)
from ...copilot.traffic_tools import (
    SimulationServiceTrafficDataSource,
    TrafficToolService,
)
from ...core.exceptions import AppError
from ...schemas.copilot import CopilotChatRequest, CopilotChatResponse
from ...services.simulation_service import SimulationService
from ..deps import get_copilot_provider, get_simulation_service

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post(
    "/simulations/{session_id}/copilot/chat",
    response_model=CopilotChatResponse,
)
def chat(
    session_id: str,
    request_body: CopilotChatRequest,
    request: Request,
    service: SimulationService = Depends(get_simulation_service),
    provider: LLMProvider = Depends(get_copilot_provider),
) -> CopilotChatResponse:
    """在指定仿真会话上执行一次只读 Copilot 问答。"""

    # 先校验会话存在，再调用模型；否则模型可能只调用 calculator/search
    # 而绕过当前会话的有效性检查。
    snapshot = service.snapshot(session_id)
    resolved_event_id = _resolve_event_context(
        snapshot,
        request_body.active_event_id,
    )
    data_source = SimulationServiceTrafficDataSource(
        service,
        history_repository=getattr(service, "history_repository", None),
        topology=getattr(request.app.state, "copilot_topology", None),
    )
    settings = request.app.state.settings
    tool_service = TrafficToolService(
        data_source,
        session_id=session_id,
        history_default_lookback_seconds=getattr(
            settings, "history_default_lookback_seconds", 300.0
        ),
        history_max_query_seconds=getattr(
            settings, "history_max_query_seconds", 3600.0
        ),
        history_max_points=getattr(settings, "history_max_points", 120),
        knowledge_retriever=getattr(request.app.state, "knowledge_retriever", None),
        knowledge_default_limit=getattr(settings, "rag_top_k", 5),
    )
    orchestrator = CopilotOrchestrator(
        provider,
        tool_service,
        max_rounds=settings.copilot_max_rounds,
        max_tool_calls=settings.copilot_max_tool_calls,
        max_tool_result_chars=settings.copilot_max_tool_result_chars,
    )

    try:
        result = orchestrator.run(
            request_body.message,
            history=[item.model_dump() for item in request_body.history],
            active_event_id=resolved_event_id,
            active_scope=request_body.active_scope,
        )
    except LLMUnavailableError as exc:
        logger.warning("Copilot Qwen service unavailable: code=%s", exc.code)
        raise AppError(
            code="COPILOT_LLM_UNAVAILABLE",
            message="Copilot 模型服务当前不可用，请稍后重试。",
            status_code=503,
        ) from exc
    except LLMProtocolError as exc:
        logger.error("Copilot Qwen protocol error: code=%s", exc.code)
        raise AppError(
            code="COPILOT_LLM_PROTOCOL_ERROR",
            message="Copilot 模型服务返回了无法识别的结果。",
            status_code=502,
        ) from exc
    except LLMInputError as exc:
        logger.error("Copilot Qwen configuration/input error: code=%s", exc.code)
        raise AppError(
            code="COPILOT_LLM_CONFIG_ERROR",
            message="Copilot 模型配置无效。",
            status_code=503,
        ) from exc
    except CopilotInputError as exc:
        raise AppError(
            code="COPILOT_REQUEST_INVALID",
            message=exc.message,
            status_code=422,
        ) from exc
    except CopilotLimitError as exc:
        raise AppError(
            code=exc.code,
            message=exc.message,
            status_code=422,
        ) from exc
    except CopilotModelError as exc:
        logger.error("Copilot model error: code=%s", exc.code)
        raise AppError(
            code="COPILOT_MODEL_ERROR",
            message="Copilot 暂时无法生成回答，请稍后重试。",
            status_code=502,
        ) from exc
    except CopilotError as exc:
        logger.error("Copilot error: code=%s", exc.code)
        raise AppError(
            code="COPILOT_ERROR",
            message="Copilot 请求未能完成。",
            status_code=502,
        ) from exc
    except LLMError as exc:
        logger.error("Unhandled Copilot LLM error: code=%s", exc.code)
        raise AppError(
            code="COPILOT_LLM_ERROR",
            message="Copilot 模型请求失败，请稍后重试。",
            status_code=502,
        ) from exc

    return CopilotChatResponse(
        session_id=session_id,
        answer=result.answer,
        rounds=result.rounds,
        tool_calls=[item.as_dict() for item in result.tool_calls],
        model=result.model,
        usage=dict(result.usage),
        latency_ms=result.latency_ms,
    )


def _resolve_event_context(
    snapshot: Mapping[str, Any] | Any,
    explicit_event_id: str | None = None,
) -> str | None:
    """Resolve a safe event context for conversational references.

    The UI can provide an exact event ID and that value always wins. When it
    does not, infer an ID only when the snapshot contains one unambiguous
    active event, or one event in total. Event type words such as ``accident``
    are never converted into IDs here, and multiple events remain ambiguous.
    """

    if isinstance(explicit_event_id, str) and explicit_event_id.strip():
        return explicit_event_id.strip()

    event_ids: set[str] = set()
    active_event_ids: set[str] = set()
    configured_event_ids: set[str] = set()
    configured_active_event_ids: set[str] = set()
    active_states = {"active", "running", "armed", "triggered"}

    def collect(items: Any, *, state_field: str) -> None:
        if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
            return
        for item in items:
            if not isinstance(item, Mapping):
                continue
            raw_event_id = item.get("event_id")
            if not isinstance(raw_event_id, str) or not raw_event_id.strip():
                continue
            event_id = raw_event_id.strip()
            event_ids.add(event_id)
            state = str(item.get(state_field, "")).strip().lower()
            if state in active_states:
                active_event_ids.add(event_id)

    configured_events = (
        snapshot.get("events", ()) if isinstance(snapshot, Mapping) else ()
    )
    collect(configured_events, state_field="state")
    if isinstance(configured_events, Sequence) and not isinstance(
        configured_events, (str, bytes)
    ):
        for item in configured_events:
            if not isinstance(item, Mapping):
                continue
            raw_event_id = item.get("event_id")
            if not isinstance(raw_event_id, str) or not raw_event_id.strip():
                continue
            event_id = raw_event_id.strip()
            configured_event_ids.add(event_id)
            state = str(item.get("state", "")).strip().lower()
            if state in active_states:
                configured_active_event_ids.add(event_id)
    detection = (
        snapshot.get("event_detection", {})
        if isinstance(snapshot, Mapping)
        else {}
    )
    collect(
        detection.get("cards", ()) if isinstance(detection, Mapping) else (),
        state_field="status",
    )

    # Configured disturbances are the authoritative event context. Detection
    # cards may describe the same physical disturbance with a generated ID;
    # do not let that derived card replace the one configured by the user.
    if len(configured_active_event_ids) == 1:
        return next(iter(configured_active_event_ids))
    if len(configured_event_ids) == 1:
        return next(iter(configured_event_ids))
    if len(active_event_ids) == 1:
        return next(iter(active_event_ids))
    if len(event_ids) == 1:
        return next(iter(event_ids))
    return None
