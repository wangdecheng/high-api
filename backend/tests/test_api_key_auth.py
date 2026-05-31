import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_key import ApiKey
from app.models.provider import Provider
from app.models.model import Model
from app.services.auth_service import register
from app.services.api_key_service import create_api_key, revoke_api_key


async def _ensure_test_model(db: AsyncSession, user_id: int | None = None) -> None:
    """Seed the test model + provider needed by the proxy endpoint.
    Optionally top up the user's balance so the proxy call isn't rejected."""
    result = await db.execute(select(Provider).where(Provider.name == "OpenAI").limit(1))
    provider = result.scalar_one_or_none()
    if not provider:
        provider = Provider(
            name="OpenAI",
            api_base_url="https://api.openai.com",
            status="active",
        )
        db.add(provider)
        await db.flush()

    result = await db.execute(
        select(Model).where(Model.public_name == "gpt-4o-mini").limit(1)
    )
    if not result.scalar_one_or_none():
        model = Model(
            public_name="gpt-4o-mini",
            provider_id=provider.id,
            provider_model_id="gpt-4o-mini",
            description="Test model",
            input_price=150,
            output_price=600,
            status="active",
        )
        db.add(model)
        await db.commit()

    if user_id is not None:
        from app.models.user import User
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one()
        user.balance = 10000  # ¥100 — more than enough for test calls
        await db.commit()


class TestApiKeyAuth:
    @pytest.mark.asyncio
    async def test_valid_api_key_accepted(self, async_client: AsyncClient, async_session: AsyncSession):
        """Proxy endpoint accepts a valid sk key."""
        user = await register(async_session, email="auth-test@example.com", password="securepass123")
        _, raw_key = await create_api_key(async_session, user=user, name="test-key")
        await _ensure_test_model(async_session, user_id=user.id)

        response = await async_client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["object"] == "chat.completion"
        assert data["model"] == "gpt-4o-mini"
        assert len(data["choices"]) == 1
        assert data["choices"][0]["message"]["role"] == "assistant"
        assert "usage" in data

    @pytest.mark.asyncio
    async def test_missing_auth_header(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )
        assert response.status_code == 401
        assert response.json()["code"] == "MISSING_API_KEY"

    @pytest.mark.asyncio
    async def test_invalid_key_format(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers={"Authorization": "Bearer not-a-valid-sk-key"},
        )
        assert response.status_code == 401
        assert response.json()["code"] == "INVALID_API_KEY_FORMAT"

    @pytest.mark.asyncio
    async def test_wrong_key_rejected(self, async_client: AsyncClient, async_session: AsyncSession):
        """A made-up key that has correct format but doesn't match any hash."""
        user = await register(async_session, email="wrong-key@example.com", password="securepass123")
        await create_api_key(async_session, user=user, name="real-key")

        # Use a valid-format but non-existent key
        fake_key = "sk-" + "f" * 40
        response = await async_client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers={"Authorization": f"Bearer {fake_key}"},
        )
        assert response.status_code == 401
        assert response.json()["code"] == "INVALID_API_KEY"

    @pytest.mark.asyncio
    async def test_revoked_key_rejected(self, async_client: AsyncClient, async_session: AsyncSession):
        user = await register(async_session, email="revoked-auth@example.com", password="securepass123")
        _, raw_key = await create_api_key(async_session, user=user, name="to-revoke")

        # Revoke the key
        result = await async_session.execute(
            select(ApiKey).where(ApiKey.user_id == user.id)
        )
        key = result.scalars().first()
        await revoke_api_key(async_session, user=user, key_id=key.id)

        response = await async_client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert response.status_code == 401
        assert response.json()["code"] == "INVALID_API_KEY"

    @pytest.mark.asyncio
    async def test_updates_last_used_at(self, async_client: AsyncClient, async_session: AsyncSession):
        """Using a key should update its last_used_at timestamp."""
        user = await register(async_session, email="last-used@example.com", password="securepass123")
        api_key, raw_key = await create_api_key(async_session, user=user, name="usage-key")
        await _ensure_test_model(async_session, user_id=user.id)

        assert api_key.last_used_at is None

        response = await async_client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert response.status_code == 200

        # Refresh and verify last_used_at updated
        await async_session.refresh(api_key)
        assert api_key.last_used_at is not None


class TestProxyValidation:
    @pytest.mark.asyncio
    async def test_empty_messages_rejected(self, async_client: AsyncClient, async_session: AsyncSession):
        user = await register(async_session, email="validation@example.com", password="securepass123")
        _, raw_key = await create_api_key(async_session, user=user, name="test-key")

        response = await async_client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "messages": [],
            },
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_model_rejected(self, async_client: AsyncClient, async_session: AsyncSession):
        user = await register(async_session, email="no-model@example.com", password="securepass123")
        _, raw_key = await create_api_key(async_session, user=user, name="test-key")

        response = await async_client.post(
            "/api/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert response.status_code == 422
