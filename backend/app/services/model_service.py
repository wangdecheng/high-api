import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import AppException
from app.models.model import Model, ChannelConfig
from app.models.provider import Provider

logger = logging.getLogger("high-api")


async def list_active_models(db: AsyncSession) -> list[dict]:
    """Return all active models with their channel configurations.

    Each model includes a list of channels (provider_name, multiplier, is_default).
    Models with status != 'active' are excluded.
    """
    # Fetch all active models
    model_result = await db.execute(
        select(Model).where(Model.status == "active").order_by(Model.public_name)
    )
    models = model_result.scalars().all()

    if not models:
        return []

    # Fetch all channels for these models in one query
    model_ids = [m.id for m in models]
    channel_result = await db.execute(
        select(ChannelConfig, Provider.name)
        .join(Provider, ChannelConfig.provider_id == Provider.id)
        .where(
            ChannelConfig.model_id.in_(model_ids),
            ChannelConfig.status == "active",
            Provider.status == "active",
            ChannelConfig.multiplier > 0,
        )
    )
    channel_rows = channel_result.all()

    # Group channels by model_id
    channels_by_model: dict[int, list[dict]] = {}
    for ch_config, provider_name in channel_rows:
        channels_by_model.setdefault(ch_config.model_id, []).append({
            "id": ch_config.id,
            "model_id": ch_config.model_id,
            "provider_name": provider_name,
            "multiplier": ch_config.multiplier,
            "is_default": ch_config.is_default,
        })

    default_counts: dict[int, int] = {}
    for channels in channels_by_model.values():
        for channel in channels:
            if channel["is_default"]:
                default_counts[channel["model_id"]] = default_counts.get(channel["model_id"], 0) + 1

    # Assemble response
    result = []
    for model in models:
        channels = channels_by_model.get(model.id, [])
        default_count = default_counts.get(model.id, 0)
        if not channels or default_count != 1:
            logger.warning(
                "Model %s (id=%d) hidden: channels=%d, default_channels=%d (expected exactly 1 default)",
                model.public_name, model.id, len(channels), default_count,
            )
            continue
        # Sort: default channel first, then by multiplier ascending
        channels.sort(key=lambda c: (not c["is_default"], c["multiplier"]))
        result.append({
            "id": model.id,
            "public_name": model.public_name,
            "description": model.description,
            "input_price": model.input_price,
            "output_price": model.output_price,
            "channels": [
                {
                    "id": channel["id"],
                    "provider_name": channel["provider_name"],
                    "multiplier": channel["multiplier"],
                    "is_default": channel["is_default"],
                }
                for channel in channels
            ],
        })

    return result


async def get_model_detail(db: AsyncSession, model_id: int) -> dict:
    """Return full detail for a single model, including all channels."""
    result = await db.execute(
        select(Model).where(Model.id == model_id, Model.status == "active")
    )
    model = result.scalar_one_or_none()
    if not model:
        raise AppException(status_code=404, error="模型不存在", code="MODEL_NOT_FOUND")

    # Get native provider name
    provider_result = await db.execute(
        select(Provider.name).where(Provider.id == model.provider_id, Provider.status == "active")
    )
    provider_name = provider_result.scalar_one_or_none()
    if not provider_name:
        raise AppException(status_code=404, error="模型不存在", code="MODEL_NOT_FOUND")

    # Get all channels
    channel_result = await db.execute(
        select(ChannelConfig, Provider.name)
        .join(Provider, ChannelConfig.provider_id == Provider.id)
        .where(
            ChannelConfig.model_id == model.id,
            ChannelConfig.status == "active",
            Provider.status == "active",
            ChannelConfig.multiplier > 0,
        )
    )
    channel_rows = channel_result.all()

    channels = []
    for ch_config, ch_provider_name in channel_rows:
        channels.append({
            "id": ch_config.id,
            "provider_name": ch_provider_name,
            "multiplier": ch_config.multiplier,
            "is_default": ch_config.is_default,
        })
    channels.sort(key=lambda c: (not c["is_default"], c["multiplier"]))
    if not channels or sum(1 for channel in channels if channel["is_default"]) != 1:
        raise AppException(status_code=404, error="模型不存在", code="MODEL_NOT_FOUND")

    return {
        "id": model.id,
        "public_name": model.public_name,
        "provider_name": provider_name,
        "provider_model_id": model.provider_model_id,
        "description": model.description,
        "input_price": model.input_price,
        "output_price": model.output_price,
        "status": model.status,
        "channels": channels,
        "created_at": model.created_at,
    }
