import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.auth_service import register
from app.services.api_key_service import create_api_key
from app.services.usage_service import record_usage, get_user_usage_stats, get_user_usage_history


class TestUsageService:
    @pytest.mark.asyncio
    async def test_record_usage(self, async_session: AsyncSession):
        user = await register(async_session, email="usage-svc@example.com", password="securepass123")
        api_key, _ = await create_api_key(async_session, user=user, name="key")

        record = await record_usage(
            db=async_session,
            user_id=user.id,
            api_key_id=api_key.id,
            model="gpt-4o",
            request_tokens=100,
            response_tokens=50,
            cost_cents=20,
        )

        assert record.id is not None
        assert record.model == "gpt-4o"
        assert record.request_tokens == 100
        assert record.response_tokens == 50
        assert record.total_tokens == 150
        assert record.cost_cents == 20
        assert record.user_id == user.id

    @pytest.mark.asyncio
    async def test_get_user_usage_stats(self, async_session: AsyncSession):
        user = await register(async_session, email="stats@example.com", password="securepass123")
        api_key, _ = await create_api_key(async_session, user=user, name="key")

        await record_usage(
            db=async_session,
            user_id=user.id,
            api_key_id=api_key.id,
            model="gpt-4o",
            request_tokens=100,
            response_tokens=50,
            cost_cents=20,
        )
        await record_usage(
            db=async_session,
            user_id=user.id,
            api_key_id=api_key.id,
            model="claude-sonnet-4-6",
            request_tokens=200,
            response_tokens=100,
            cost_cents=15,
        )

        stats = await get_user_usage_stats(db=async_session, user=user)

        assert stats["today_calls"] == 2
        assert stats["today_tokens"] == 450  # 150 + 300
        assert stats["today_cost_cents"] == 35  # 20 + 15
        assert stats["active_keys"] == 1
        assert len(stats["daily"]) >= 1

    @pytest.mark.asyncio
    async def test_get_user_usage_stats_empty(self, async_session: AsyncSession):
        user = await register(async_session, email="no-usage@example.com", password="securepass123")

        stats = await get_user_usage_stats(db=async_session, user=user)
        assert stats["today_calls"] == 0
        assert stats["today_tokens"] == 0
        assert stats["today_cost_cents"] == 0
        assert stats["active_keys"] == 0
        assert stats["daily"] == []

    @pytest.mark.asyncio
    async def test_get_user_usage_history(self, async_session: AsyncSession):
        user = await register(async_session, email="history@example.com", password="securepass123")
        api_key, _ = await create_api_key(async_session, user=user, name="key")

        for i in range(5):
            await record_usage(
                db=async_session,
                user_id=user.id,
                api_key_id=api_key.id,
                model=f"model-{i}",
                request_tokens=10 * (i + 1),
                response_tokens=5 * (i + 1),
                cost_cents=i,
            )

        records, total = await get_user_usage_history(
            db=async_session, user=user, page=1, page_size=3,
        )
        assert len(records) == 3
        assert total == 5

        # Page 2
        records2, _ = await get_user_usage_history(
            db=async_session, user=user, page=2, page_size=3,
        )
        assert len(records2) == 2

    @pytest.mark.asyncio
    async def test_usage_stats_only_own_user(self, async_session: AsyncSession):
        user1 = await register(async_session, email="isolated-1@example.com", password="securepass123")
        user2 = await register(async_session, email="isolated-2@example.com", password="securepass123")
        key1, _ = await create_api_key(async_session, user=user1, name="k1")
        key2, _ = await create_api_key(async_session, user=user2, name="k2")

        await record_usage(async_session, user_id=user1.id, api_key_id=key1.id, model="m", cost_cents=100)
        await record_usage(async_session, user_id=user2.id, api_key_id=key2.id, model="m", cost_cents=200)

        stats1 = await get_user_usage_stats(db=async_session, user=user1)
        assert stats1["today_calls"] == 1
        assert stats1["today_cost_cents"] == 100

        stats2 = await get_user_usage_stats(db=async_session, user=user2)
        assert stats2["today_calls"] == 1
        assert stats2["today_cost_cents"] == 200


class TestUsageApi:
    @pytest.mark.asyncio
    async def test_stats_endpoint(self, async_client: AsyncClient, async_session: AsyncSession):
        """GET /api/usage/stats returns stats for authenticated user."""
        await async_client.post(
            "/api/auth/register",
            json={
                "email": "usage-api@example.com",
                "password": "securepass123",
                "confirmPassword": "securepass123",
            },
        )

        response = await async_client.get("/api/usage/stats")
        assert response.status_code == 200
        data = response.json()
        assert "todayCalls" in data
        assert "todayTokens" in data
        assert "todayCostCents" in data
        assert "activeKeys" in data
        assert "daily" in data
        assert isinstance(data["daily"], list)

    @pytest.mark.asyncio
    async def test_stats_401_unauthenticated(self, async_client: AsyncClient):
        response = await async_client.get("/api/usage/stats")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_history_endpoint(self, async_client: AsyncClient):
        await async_client.post(
            "/api/auth/register",
            json={
                "email": "history-api@example.com",
                "password": "securepass123",
                "confirmPassword": "securepass123",
            },
        )

        response = await async_client.get("/api/usage/history")
        assert response.status_code == 200
        data = response.json()
        assert "records" in data
        assert "total" in data
        assert "page" in data
        assert "pageSize" in data
        assert isinstance(data["records"], list)

    @pytest.mark.asyncio
    async def test_history_with_pagination(self, async_client: AsyncClient):
        await async_client.post(
            "/api/auth/register",
            json={
                "email": "page-api@example.com",
                "password": "securepass123",
                "confirmPassword": "securepass123",
            },
        )

        response = await async_client.get("/api/usage/history?page=1&pageSize=10")
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 1
        assert data["pageSize"] == 10

    @pytest.mark.asyncio
    async def test_days_query_param(self, async_client: AsyncClient):
        await async_client.post(
            "/api/auth/register",
            json={
                "email": "days-api@example.com",
                "password": "securepass123",
                "confirmPassword": "securepass123",
            },
        )

        response = await async_client.get("/api/usage/stats?days=7")
        assert response.status_code == 200
