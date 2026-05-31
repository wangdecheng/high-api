import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.auth_service import register
from app.services.api_key_service import (
    generate_key,
    hash_key,
    create_api_key,
    list_api_keys,
    revoke_api_key,
)
from app.main import AppException


class TestGenerateKey:
    def test_generates_sk_prefix(self):
        raw, prefix = generate_key()
        assert raw.startswith("sk-")
        assert prefix.startswith("sk-")
        assert len(prefix) == 10
        assert raw[:10] == prefix

    def test_generates_unique_keys(self):
        raw1, _ = generate_key()
        raw2, _ = generate_key()
        assert raw1 != raw2


class TestHashKey:
    def test_hash_is_bcrypt(self):
        h = hash_key("sk-testkey123")
        assert h.startswith("$2b$") or h.startswith("$2a$")

    def test_hash_is_deterministic_check(self):
        h = hash_key("sk-testkey123")
        import bcrypt
        assert bcrypt.checkpw("sk-testkey123".encode(), h.encode())


class TestCreateApiKey:
    @pytest.mark.asyncio
    async def test_creates_key(self, async_session: AsyncSession):
        user = await register(async_session, email="key-user@example.com", password="securepass123")
        api_key, raw_key = await create_api_key(async_session, user=user, name="my-key")

        assert api_key.id is not None
        assert api_key.name == "my-key"
        assert api_key.user_id == user.id
        assert api_key.status == "active"
        assert raw_key.startswith("sk-")
        assert api_key.key_prefix == raw_key[:10]

    @pytest.mark.asyncio
    async def test_hash_matches_raw_key(self, async_session: AsyncSession):
        user = await register(async_session, email="key-user2@example.com", password="securepass123")
        api_key, raw_key = await create_api_key(async_session, user=user, name="test-key")

        import bcrypt
        assert bcrypt.checkpw(raw_key.encode(), api_key.key_hash.encode())


class TestListApiKeys:
    @pytest.mark.asyncio
    async def test_lists_only_user_keys(self, async_session: AsyncSession):
        user1 = await register(async_session, email="u1@example.com", password="securepass123")
        user2 = await register(async_session, email="u2@example.com", password="securepass123")

        await create_api_key(async_session, user=user1, name="k1")
        await create_api_key(async_session, user=user1, name="k2")
        await create_api_key(async_session, user=user2, name="k3")

        keys_u1 = await list_api_keys(async_session, user=user1)
        assert len(keys_u1) == 2
        assert all(k.user_id == user1.id for k in keys_u1)

    @pytest.mark.asyncio
    async def test_excludes_revoked_keys(self, async_session: AsyncSession):
        user = await register(async_session, email="u3@example.com", password="securepass123")
        api_key, _ = await create_api_key(async_session, user=user, name="active-key")
        await create_api_key(async_session, user=user, name="revoked-key")

        # Revoke the second one
        all_keys = await list_api_keys(async_session, user=user)
        second_key = [k for k in all_keys if k.name == "revoked-key"][0]
        await revoke_api_key(async_session, user=user, key_id=second_key.id)

        active = await list_api_keys(async_session, user=user)
        assert len(active) == 1
        assert active[0].name == "active-key"


class TestRevokeApiKey:
    @pytest.mark.asyncio
    async def test_revokes_key(self, async_session: AsyncSession):
        user = await register(async_session, email="u4@example.com", password="securepass123")
        api_key, _ = await create_api_key(async_session, user=user, name="to-revoke")

        await revoke_api_key(async_session, user=user, key_id=api_key.id)

        # Verify status changed
        await async_session.refresh(api_key)
        assert api_key.status == "revoked"

    @pytest.mark.asyncio
    async def test_revoke_other_users_key_fails(self, async_session: AsyncSession):
        user1 = await register(async_session, email="u5@example.com", password="securepass123")
        user2 = await register(async_session, email="u6@example.com", password="securepass123")
        api_key, _ = await create_api_key(async_session, user=user1, name="k")

        with pytest.raises(AppException) as exc_info:
            await revoke_api_key(async_session, user=user2, key_id=api_key.id)
        assert exc_info.value.status_code == 404
        assert exc_info.value.code == "KEY_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_key(self, async_session: AsyncSession):
        user = await register(async_session, email="u7@example.com", password="securepass123")
        with pytest.raises(AppException) as exc_info:
            await revoke_api_key(async_session, user=user, key_id=99999)
        assert exc_info.value.code == "KEY_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_revoke_already_revoked(self, async_session: AsyncSession):
        user = await register(async_session, email="u8@example.com", password="securepass123")
        api_key, _ = await create_api_key(async_session, user=user, name="k")
        await revoke_api_key(async_session, user=user, key_id=api_key.id)

        with pytest.raises(AppException) as exc_info:
            await revoke_api_key(async_session, user=user, key_id=api_key.id)
        assert exc_info.value.code == "KEY_ALREADY_REVOKED"
