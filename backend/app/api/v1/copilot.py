"""Traffic Copilot 对话 API。"""

from __future__ import annotations

import logging

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
    service.snapshot(session_id)
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
            active_event_id=request_body.active_event_id,
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
