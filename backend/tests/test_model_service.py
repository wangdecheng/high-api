import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider import Provider
from app.models.model import Model, ChannelConfig
from app.services.model_service import list_active_models, get_model_detail
from app.main import AppException


async def _seed_test_data(db: AsyncSession):
    """Create test providers, models, and channel configs."""
    # Providers
    p1 = Provider(name="Anthropic", api_base_url="https://api.anthropic.com", status="active")
    p2 = Provider(name="OpenAI", api_base_url="https://api.openai.com", status="active")
    db.add_all([p1, p2])
    await db.flush()

    # Models
    m1 = Model(
        public_name="claude-sonnet-4-6",
        provider_id=p1.id,
        provider_model_id="claude-sonnet-4-6-20250501",
        description="Balanced model",
        input_price=3_000,
        output_price=15_000,
        status="active",
    )
    m2 = Model(
        public_name="gpt-4o-mini",
        provider_id=p2.id,
        provider_model_id="gpt-4o-mini",
        description="Small affordable model",
        input_price=150,
        output_price=600,
        status="active",
    )
    m3 = Model(
        public_name="claude-legacy",
        provider_id=p1.id,
        provider_model_id="claude-legacy",
        description="Inactive model",
        input_price=1_000,
        output_price=2_000,
        status="inactive",
    )
    db.add_all([m1, m2, m3])
    await db.flush()

    # Channel configs
    ch1 = ChannelConfig(model_id=m1.id, provider_id=p1.id, multiplier=1.0, is_default=True)
    ch2 = ChannelConfig(model_id=m2.id, provider_id=p2.id, multiplier=1.0, is_default=True)
    ch3 = ChannelConfig(model_id=m3.id, provider_id=p1.id, multiplier=1.0, is_default=True)
    db.add_all([ch1, ch2, ch3])
    await db.commit()


class TestListActiveModels:
    @pytest.mark.asyncio
    async def test_lists_only_active_models(self, async_session: AsyncSession):
        await _seed_test_data(async_session)
        models = await list_active_models(async_session)
        assert len(models) == 2
        names = [m["public_name"] for m in models]
        assert "claude-sonnet-4-6" in names
        assert "gpt-4o-mini" in names
        assert "claude-legacy" not in names

    @pytest.mark.asyncio
    async def test_each_model_has_channels(self, async_session: AsyncSession):
        await _seed_test_data(async_session)
        models = await list_active_models(async_session)
        for model in models:
            assert len(model["channels"]) >= 1
            ch = model["channels"][0]
            assert "provider_name" in ch
            assert "multiplier" in ch
            assert "is_default" in ch

    @pytest.mark.asyncio
    async def test_default_channel_first(self, async_session: AsyncSession):
        """Default channel should be sorted first."""
        await _seed_test_data(async_session)
        models = await list_active_models(async_session)
        for model in models:
            if model["channels"]:
                assert model["channels"][0]["is_default"] is True


    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_active_models(self, async_session: AsyncSession):
        # No seed data → empty list
        models = await list_active_models(async_session)
        assert models == []

    @pytest.mark.asyncio
    async def test_filters_inactive_provider_channels(self, async_session: AsyncSession):
        await _seed_test_data(async_session)
        from sqlalchemy import select

        provider_result = await async_session.execute(
            select(Provider).where(Provider.name == "OpenAI")
        )
        provider = provider_result.scalar_one()
        provider.status = "inactive"
        await async_session.commit()

        models = await list_active_models(async_session)
        names = [m["public_name"] for m in models]
        assert "gpt-4o-mini" not in names

    @pytest.mark.asyncio
    async def test_filters_models_without_default_channel(self, async_session: AsyncSession):
        p = Provider(name="NoDefaultAI", api_base_url="https://api.no-default.test", status="active")
        async_session.add(p)
        await async_session.flush()
        m = Model(
            public_name="no-default-model",
            provider_id=p.id,
            provider_model_id="no-default-model",
            description="No default channel",
            input_price=1_000,
            output_price=2_000,
            status="active",
        )
        async_session.add(m)
        await async_session.flush()
        async_session.add(
            ChannelConfig(
                model_id=m.id,
                provider_id=p.id,
                multiplier=1.0,
                status="active",
                is_default=False,
            )
        )
        await async_session.commit()

        models = await list_active_models(async_session)
        assert all(model["public_name"] != "no-default-model" for model in models)


class TestGetModelDetail:
    @pytest.mark.asyncio
    async def test_returns_full_detail(self, async_session: AsyncSession):
        await _seed_test_data(async_session)
        # Get the first active model
        active = await list_active_models(async_session)
        model_id = active[0]["id"]

        detail = await get_model_detail(async_session, model_id)
        assert detail["id"] == model_id
        assert detail["public_name"] is not None
        assert detail["provider_name"] is not None
        assert detail["provider_model_id"] is not None
        assert "channels" in detail
        assert len(detail["channels"]) >= 1

    @pytest.mark.asyncio
    async def test_404_for_nonexistent_model(self, async_session: AsyncSession):
        with pytest.raises(AppException) as exc_info:
            await get_model_detail(async_session, 99999)
        assert exc_info.value.status_code == 404
        assert exc_info.value.code == "MODEL_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_inactive_model_returns_404_by_id(self, async_session: AsyncSession):
        """Inactive models are hidden from the public detail endpoint."""
        await _seed_test_data(async_session)
        # Find the inactive model
        from sqlalchemy import select
        result = await async_session.execute(
            select(Model).where(Model.status == "inactive")
        )
        inactive = result.scalar_one()

        with pytest.raises(AppException) as exc_info:
            await get_model_detail(async_session, inactive.id)
        assert exc_info.value.status_code == 404
        assert exc_info.value.code == "MODEL_NOT_FOUND"
