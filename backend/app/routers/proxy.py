import math
import time
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user_from_api_key
from app.main import AppException
from app.models.api_key import ApiKey
from app.models.model import Model
from app.models.user import User
from app.schemas.proxy import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionChoice,
    ChatCompletionUsage,
    ChatMessage,
)
from app.schemas.common import ErrorResponse
from app.services.usage_service import record_usage

router = APIRouter(prefix="/api/v1", tags=["proxy"])


def _estimate_tokens(text: str) -> int:
    """Rough token estimation: ~4 chars per token for English, ~2 for Chinese."""
    return max(1, len(text.encode("utf-8")) // 3)


async def _lookup_model_pricing(
    db: AsyncSession, model_name: str
) -> tuple[int, int] | None:
    """Look up model pricing from database. Returns (input_price, output_price)
    in micro-yuan per 1K tokens, or None if model not found."""
    result = await db.execute(
        select(Model.input_price, Model.output_price).where(
            Model.public_name == model_name,
            Model.status == "active",
        )
    )
    row = result.one_or_none()
    if not row:
        return None
    return row.input_price, row.output_price


def _compute_cost(
    input_price_micro_yuan: int,
    output_price_micro_yuan: int,
    request_tokens: int,
    response_tokens: int,
) -> int:
    """Compute cost in micro-yuan (1/10000 cent) and convert to cents, rounding up.

    Database stores prices in micro-yuan per 1K tokens.
    Returns cost in cents (分).
    """
    # micro-yuan per 1K tokens → micro-yuan for actual tokens
    input_cost_uy = (request_tokens / 1000) * input_price_micro_yuan
    output_cost_uy = (response_tokens / 1000) * output_price_micro_yuan
    # micro-yuan → cents: divide by 10000, round up
    cost_cents = math.ceil((input_cost_uy + output_cost_uy) / 10_000)
    return max(0, cost_cents)


# Cost cap: maximum 200 RMB (20000 cents) per request
MAX_COST_CENTS = 20_000


@router.post(
    "/chat/completions",
    response_model=ChatCompletionResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Invalid API key"},
        422: {"model": ErrorResponse, "description": "Validation error"},
    },
)
async def chat_completions(
    body: ChatCompletionRequest,
    auth: tuple[User, ApiKey] = Depends(get_current_user_from_api_key),
    db: AsyncSession = Depends(get_db),
):
    """OpenAI-compatible chat completions endpoint.

    Authenticate via API key (Bearer sk-...), proxy to upstream AI,
    and record usage.
    """
    user, api_key = auth

    # Look up model pricing from database
    pricing = await _lookup_model_pricing(db, body.model)
    if not pricing:
        raise AppException(
            status_code=400,
            error=f"不支持的模型: {body.model}",
            code="UNSUPPORTED_MODEL",
        )

    # Estimate request tokens from all messages
    request_text = " ".join(m.content for m in body.messages)
    request_tokens = _estimate_tokens(request_text)

    # --- Simulated response (replace with real upstream call) ---
    # In production, this would forward the request to the actual AI provider.
    last_message = body.messages[-1]
    response_content = (
        f"[模拟响应] 您的问题已收到。模型: {body.model}，"
        f"消息数: {len(body.messages)}，输入约 {request_tokens} tokens。"
        f"（这是 API 中转服务的模拟响应，实际部署后将转发至上游 AI 提供商。）"
    )
    response_tokens = _estimate_tokens(response_content)
    # --- End simulated response ---

    # Compute cost from database pricing
    input_price, output_price = pricing
    cost_cents = _compute_cost(input_price, output_price, request_tokens, response_tokens)

    # Cost cap check
    if cost_cents > MAX_COST_CENTS:
        cost_cents = MAX_COST_CENTS

    # Check and deduct balance
    if cost_cents > 0:
        if user.balance < cost_cents:
            raise AppException(
                status_code=402,
                error=f"余额不足，需要 ¥{cost_cents / 100:.2f}，当前余额 ¥{user.balance / 100:.2f}",
                code="INSUFFICIENT_BALANCE",
            )
        user.balance -= cost_cents

    # Record usage
    await record_usage(
        db=db,
        user_id=user.id,
        api_key_id=api_key.id,
        model=body.model,
        request_tokens=request_tokens,
        response_tokens=response_tokens,
        cost_cents=cost_cents,
    )

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"

    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": response_content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": request_tokens,
            "completion_tokens": response_tokens,
            "total_tokens": request_tokens + response_tokens,
        },
    }
