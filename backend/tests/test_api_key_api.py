import pytest
from httpx import AsyncClient


class TestCreateKeyEndpoint:
    @pytest.mark.asyncio
    async def test_create_key_201(self, async_client: AsyncClient):
        # Register to get a session cookie
        await async_client.post(
            "/api/auth/register",
            json={
                "email": "key-api@example.com",
                "password": "securepass123",
                "confirmPassword": "securepass123",
            },
        )
        response = await async_client.post(
            "/api/keys",
            json={"name": "my-production-key"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "my-production-key"
        assert data["keyPrefix"].startswith("sk-")
        assert data["rawKey"].startswith("sk-")
        assert data["status"] == "active"
        assert "createdAt" in data
        # rawKey should only be returned once
        assert len(data["rawKey"]) > 20

    @pytest.mark.asyncio
    async def test_create_key_401_unauthenticated(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/keys",
            json={"name": "unauthorized-key"},
        )
        assert response.status_code == 401


class TestListKeysEndpoint:
    @pytest.mark.asyncio
    async def test_list_keys_200(self, async_client: AsyncClient):
        await async_client.post(
            "/api/auth/register",
            json={
                "email": "list-api@example.com",
                "password": "securepass123",
                "confirmPassword": "securepass123",
            },
        )
        # Create two keys
        await async_client.post("/api/keys", json={"name": "key-one"})
        await async_client.post("/api/keys", json={"name": "key-two"})

        response = await async_client.get("/api/keys")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        # Sort by name for deterministic assertion (SQLite timestamps have second
        # granularity, so ORDER BY created_at DESC is non-deterministic for keys
        # created in the same second.)
        data.sort(key=lambda k: k["name"])
        assert data[0]["name"] == "key-one"
        assert data[1]["name"] == "key-two"
        # rawKey should NOT be in list response
        assert "rawKey" not in data[0]

    @pytest.mark.asyncio
    async def test_list_keys_401_unauthenticated(self, async_client: AsyncClient):
        response = await async_client.get("/api/keys")
        assert response.status_code == 401


class TestRevokeKeyEndpoint:
    @pytest.mark.asyncio
    async def test_revoke_key_200(self, async_client: AsyncClient):
        await async_client.post(
            "/api/auth/register",
            json={
                "email": "revoke-api@example.com",
                "password": "securepass123",
                "confirmPassword": "securepass123",
            },
        )
        create_resp = await async_client.post("/api/keys", json={"name": "to-revoke"})
        key_id = create_resp.json()["id"]

        response = await async_client.delete(f"/api/keys/{key_id}")
        assert response.status_code == 200
        assert response.json() == {"message": "密钥已撤销"}

        # Key should no longer appear in list
        list_resp = await async_client.get("/api/keys")
        assert len(list_resp.json()) == 0

    @pytest.mark.asyncio
    async def test_revoke_key_404(self, async_client: AsyncClient):
        await async_client.post(
            "/api/auth/register",
            json={
                "email": "revoke-404@example.com",
                "password": "securepass123",
                "confirmPassword": "securepass123",
            },
        )
        response = await async_client.delete("/api/keys/99999")
        assert response.status_code == 404
        assert response.json()["code"] == "KEY_NOT_FOUND"
