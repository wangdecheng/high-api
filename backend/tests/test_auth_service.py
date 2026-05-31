import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.auth_service import (
    register,
    login,
    hash_password,
    verify_password,
    generate_jwt,
    decode_jwt,
    generate_reset_token,
    decode_reset_token,
    change_password,
    forgot_password,
    reset_password,
)
from app.main import AppException


class TestHashPassword:
    def test_hash_returns_bcrypt_string(self):
        hashed = hash_password("testpass123")
        assert hashed.startswith("$2b$") or hashed.startswith("$2a$")

    def test_verify_correct_password(self):
        hashed = hash_password("testpass123")
        assert verify_password("testpass123", hashed) is True

    def test_verify_incorrect_password(self):
        hashed = hash_password("testpass123")
        assert verify_password("wrongpass", hashed) is False


class TestGenerateJwt:
    def test_generates_valid_token(self):
        token = generate_jwt(user_id=1, role="user")
        assert isinstance(token, str)
        assert len(token) > 0

    def test_token_contains_correct_payload(self):
        import jwt
        from app.config import settings

        token = generate_jwt(user_id=42, role="admin")
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        assert payload["user_id"] == 42
        assert payload["role"] == "admin"


class TestRegister:
    @pytest.mark.asyncio
    async def test_register_creates_user(self, async_session: AsyncSession):
        user = await register(async_session, email="test@example.com", password="securepass123")
        assert user.id is not None
        assert user.email == "test@example.com"
        assert user.role == "user"
        assert user.status == "active"
        assert user.balance == 0

    @pytest.mark.asyncio
    async def test_register_hashes_password(self, async_session: AsyncSession):
        user = await register(async_session, email="hash@example.com", password="securepass123")
        assert user.password_hash != "securepass123"
        assert user.password_hash.startswith("$2b$") or user.password_hash.startswith("$2a$")

    @pytest.mark.asyncio
    async def test_register_duplicate_email(self, async_session: AsyncSession):
        await register(async_session, email="dup@example.com", password="securepass123")
        with pytest.raises(AppException) as exc_info:
            await register(async_session, email="dup@example.com", password="otherpass456")
        assert exc_info.value.status_code == 409
        assert exc_info.value.code == "EMAIL_EXISTS"
        assert "已注册" in exc_info.value.error


class TestLogin:
    @pytest.mark.asyncio
    async def test_login_returns_user(self, async_session: AsyncSession):
        await register(async_session, email="login-test@example.com", password="securepass123")
        user = await login(async_session, email="login-test@example.com", password="securepass123")
        assert user.id is not None
        assert user.email == "login-test@example.com"
        assert user.role == "user"

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, async_session: AsyncSession):
        await register(async_session, email="wrong@example.com", password="securepass123")
        with pytest.raises(AppException) as exc_info:
            await login(async_session, email="wrong@example.com", password="incorrect")
        assert exc_info.value.status_code == 401
        assert exc_info.value.code == "INVALID_CREDENTIALS"
        assert "邮箱或密码错误" == exc_info.value.error

    @pytest.mark.asyncio
    async def test_login_nonexistent_email(self, async_session: AsyncSession):
        with pytest.raises(AppException) as exc_info:
            await login(async_session, email="nobody@example.com", password="anything")
        assert exc_info.value.status_code == 401
        assert exc_info.value.code == "INVALID_CREDENTIALS"
        assert "邮箱或密码错误" == exc_info.value.error

    @pytest.mark.asyncio
    async def test_login_same_error_for_both_cases(self, async_session: AsyncSession):
        """Ensure uniform error prevents email enumeration."""
        await register(async_session, email="enum@example.com", password="securepass123")

        # Wrong password for existing email
        with pytest.raises(AppException) as exc1:
            await login(async_session, email="enum@example.com", password="wrong")
        # Nonexistent email
        with pytest.raises(AppException) as exc2:
            await login(async_session, email="nobody@example.com", password="anything")

        assert exc1.value.error == exc2.value.error
        assert exc1.value.code == exc2.value.code


class TestDecodeJwt:
    def test_decode_valid_token(self):
        token = generate_jwt(user_id=1, role="user")
        payload = decode_jwt(token)
        assert payload["user_id"] == 1
        assert payload["role"] == "user"

    def test_decode_invalid_token(self):
        with pytest.raises(AppException) as exc_info:
            decode_jwt("not.a.valid.token")
        assert exc_info.value.status_code == 401
        assert exc_info.value.code == "INVALID_TOKEN"

    def test_decode_expired_token(self):
        import jwt as pyjwt
        from datetime import datetime, timedelta, timezone
        from app.config import settings

        expired = pyjwt.encode(
            {
                "user_id": 1,
                "role": "user",
                "exp": datetime.now(timezone.utc) - timedelta(seconds=1),
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(AppException) as exc_info:
            decode_jwt(expired)
        assert exc_info.value.status_code == 401
        assert exc_info.value.code == "TOKEN_EXPIRED"


class TestResetToken:
    def test_generate_and_decode_reset_token(self):
        token = generate_reset_token(user_id=42)
        payload = decode_reset_token(token)
        assert payload["user_id"] == 42
        assert payload["purpose"] == "password_reset"

    def test_decode_reset_token_rejects_login_jwt(self):
        login_token = generate_jwt(user_id=1, role="user")
        with pytest.raises(AppException) as exc_info:
            decode_reset_token(login_token)
        assert exc_info.value.code == "INVALID_RESET_TOKEN"

    def test_decode_expired_reset_token(self):
        import jwt as pyjwt
        from datetime import datetime, timedelta, timezone
        from app.config import settings

        expired = pyjwt.encode(
            {
                "user_id": 1,
                "purpose": "password_reset",
                "exp": datetime.now(timezone.utc) - timedelta(seconds=1),
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(AppException) as exc_info:
            decode_reset_token(expired)
        assert exc_info.value.code == "RESET_TOKEN_EXPIRED"


class TestChangePassword:
    @pytest.mark.asyncio
    async def test_change_password_success(self, async_session: AsyncSession):
        user = await register(async_session, email="cp@example.com", password="oldpass123")
        await change_password(async_session, user=user, current_password="oldpass123", new_password="newpass456")

        # Verify old password no longer works
        result = await async_session.get(type(user), user.id)
        assert result is not None
        assert not verify_password("oldpass123", result.password_hash)
        assert verify_password("newpass456", result.password_hash)

    @pytest.mark.asyncio
    async def test_change_password_wrong_current(self, async_session: AsyncSession):
        user = await register(async_session, email="cp2@example.com", password="oldpass123")
        with pytest.raises(AppException) as exc_info:
            await change_password(async_session, user=user, current_password="wrong", new_password="newpass456")
        assert exc_info.value.status_code == 400
        assert exc_info.value.code == "WRONG_PASSWORD"


class TestForgotPassword:
    @pytest.mark.asyncio
    async def test_forgot_password_returns_token(self, async_session: AsyncSession):
        await register(async_session, email="fp@example.com", password="securepass123")
        token = await forgot_password(async_session, email="fp@example.com")
        assert token is not None
        # Verify the token is valid
        payload = decode_reset_token(token)
        assert payload["purpose"] == "password_reset"

    @pytest.mark.asyncio
    async def test_forgot_password_returns_none_for_unknown_email(self, async_session: AsyncSession):
        token = await forgot_password(async_session, email="nobody@example.com")
        assert token is None


class TestResetPassword:
    @pytest.mark.asyncio
    async def test_reset_password_success(self, async_session: AsyncSession):
        user = await register(async_session, email="rp@example.com", password="oldpass123")
        token = generate_reset_token(user.id)
        await reset_password(async_session, token=token, new_password="newsecure456")

        # Verify password was changed
        await async_session.refresh(user)
        assert verify_password("newsecure456", user.password_hash)
        assert not verify_password("oldpass123", user.password_hash)

    @pytest.mark.asyncio
    async def test_reset_password_invalid_token(self, async_session: AsyncSession):
        with pytest.raises(AppException) as exc_info:
            await reset_password(async_session, token="invalid", new_password="newpass")
        assert exc_info.value.code == "INVALID_RESET_TOKEN"

    @pytest.mark.asyncio
    async def test_reset_password_nonexistent_user(self, async_session: AsyncSession):
        token = generate_reset_token(user_id=99999)
        with pytest.raises(AppException) as exc_info:
            await reset_password(async_session, token=token, new_password="newpass")
        assert exc_info.value.code == "USER_NOT_FOUND"
