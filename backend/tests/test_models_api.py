import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider import Provider
from app.models.model import Model, ChannelConfig


async def _seed_models_for_api(async_session: AsyncSession):
    """Create minimal seed data for API tests."""
    p = Provider(name="TestAI", api_base_url="https://api.test.com", status="active")
    async_session.add(p)
    await async_session.flush()

    m = Model(
        public_name="test-model",
        provider_id=p.id,
        provider_model_id="test-model-v1",
        description="A test model",
        input_price=10_000,
        output_price=20_000,
        status="active",
    )
    async_session.add(m)
    await async_session.flush()

    ch = ChannelConfig(model_id=m.id, provider_id=p.id, multiplier=1.0, is_default=True)
    async_session.add(ch)
    await async_session.commit()
    return m


class TestModelsApi:
    @pytest.mark.asyncio
    async def test_list_models_authenticated(self, async_client: AsyncClient, async_session: AsyncSession):
        """Authenticated user can list models."""
        await _seed_models_for_api(async_session)

        # Register → get cookie
        await async_client.post(
            "/api/auth/register",
            json={
                "email": "model-test@example.com",
                "password": "securepass123",
                "confirmPassword": "securepass123",
            },
        )

        response = await async_client.get("/api/models")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        model = data[0]
        assert "publicName" in model
        assert "channels" in model
        assert len(model["channels"]) >= 1

    @pytest.mark.asyncio
    async def test_list_models_401_unauthenticated(self, async_client: AsyncClient):
        response = await async_client.get("/api/models")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_model_detail(self, async_client: AsyncClient, async_session: AsyncSession):
        model = await _seed_models_for_api(async_session)

        await async_client.post(
            "/api/auth/register",
            json={
                "email": "detail-test@example.com",
                "password": "securepass123",
                "confirmPassword": "securepass123",
            },
        )

        response = await async_client.get(f"/api/models/{model.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["publicName"] == "test-model"
        assert data["providerName"] == "TestAI"
        assert data["providerModelId"] == "test-model-v1"
        assert len(data["channels"]) >= 1


    @pytest.mark.asyncio
    async def test_model_detail_401_unauthenticated(self, async_client: AsyncClient, async_session: AsyncSession):
        model = await _seed_models_for_api(async_session)

        response = await async_client.get(f"/api/models/{model.id}")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_model_detail_404(self, async_client: AsyncClient):
        await async_client.post(
            "/api/auth/register",
            json={
                "email": "detail-404@example.com",
                "password": "securepass123",
                "confirmPassword": "securepass123",
            },
        )

        response = await async_client.get("/api/models/99999")
        assert response.status_code == 404
