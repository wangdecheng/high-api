import pytest
from httpx import AsyncClient


class TestRegisterEndpoint:
    @pytest.mark.asyncio
    async def test_register_201(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/auth/register",
            json={
                "email": "api-test@example.com",
                "password": "securepass123",
                "confirmPassword": "securepass123",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["email"] == "api-test@example.com"
        assert data["balance"] == 0
        assert data["role"] == "user"
        assert "user_id" in data

        # Check that JWT cookie is set
        cookies = response.cookies
        assert "high_api_session" in cookies

    @pytest.mark.asyncio
    async def test_register_409_duplicate(self, async_client: AsyncClient):
        # First registration
        await async_client.post(
            "/api/auth/register",
            json={
                "email": "dup-api@example.com",
                "password": "securepass123",
                "confirmPassword": "securepass123",
            },
        )
        # Duplicate registration
        response = await async_client.post(
            "/api/auth/register",
            json={
                "email": "dup-api@example.com",
                "password": "securepass123",
                "confirmPassword": "securepass123",
            },
        )
        assert response.status_code == 409
        data = response.json()
        assert data["code"] == "EMAIL_EXISTS"
        assert "已注册" in data["error"]

    @pytest.mark.asyncio
    async def test_register_422_short_password(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/auth/register",
            json={
                "email": "short@example.com",
                "password": "1234567",
                "confirmPassword": "1234567",
            },
        )
        assert response.status_code == 422
        data = response.json()
        assert data["code"] == "VALIDATION_ERROR"
        assert "8位" in data["error"]

    @pytest.mark.asyncio
    async def test_register_422_password_mismatch(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/auth/register",
            json={
                "email": "mismatch@example.com",
                "password": "securepass123",
                "confirmPassword": "differentpass",
            },
        )
        assert response.status_code == 422
        data = response.json()
        assert data["code"] == "VALIDATION_ERROR"
        assert "不一致" in data["error"] or "match" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_register_422_invalid_email(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/auth/register",
            json={
                "email": "not-an-email",
                "password": "securepass123",
                "confirmPassword": "securepass123",
            },
        )
        assert response.status_code == 422
        data = response.json()
        assert data["code"] == "VALIDATION_ERROR"


class TestLoginEndpoint:
    @pytest.mark.asyncio
    async def test_login_200(self, async_client: AsyncClient):
        # Register first
        await async_client.post(
            "/api/auth/register",
            json={
                "email": "login-api@example.com",
                "password": "securepass123",
                "confirmPassword": "securepass123",
            },
        )
        # Login
        response = await async_client.post(
            "/api/auth/login",
            json={
                "email": "login-api@example.com",
                "password": "securepass123",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "login-api@example.com"
        assert data["balance"] == 0
        assert data["role"] == "user"
        assert "user_id" in data

        # Check that JWT cookie is set
        cookies = response.cookies
        assert "high_api_session" in cookies

    @pytest.mark.asyncio
    async def test_login_401_wrong_password(self, async_client: AsyncClient):
        # Register first
        await async_client.post(
            "/api/auth/register",
            json={
                "email": "wp-api@example.com",
                "password": "securepass123",
                "confirmPassword": "securepass123",
            },
        )
        # Login with wrong password
        response = await async_client.post(
            "/api/auth/login",
            json={
                "email": "wp-api@example.com",
                "password": "wrongpassword",
            },
        )
        assert response.status_code == 401
        data = response.json()
        assert data["code"] == "INVALID_CREDENTIALS"
        assert "邮箱或密码错误" == data["error"]

    @pytest.mark.asyncio
    async def test_login_401_nonexistent_email(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/auth/login",
            json={
                "email": "nobody@example.com",
                "password": "anything",
            },
        )
        assert response.status_code == 401
        data = response.json()
        assert data["code"] == "INVALID_CREDENTIALS"
        assert "邮箱或密码错误" == data["error"]

    @pytest.mark.asyncio
    async def test_login_422_missing_email(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/auth/login",
            json={
                "password": "securepass123",
            },
        )
        assert response.status_code == 422
        data = response.json()
        assert data["code"] == "VALIDATION_ERROR"


class TestMeEndpoint:
    @pytest.mark.asyncio
    async def test_me_200_returns_user(self, async_client: AsyncClient):
        # Register to get a session cookie
        await async_client.post(
            "/api/auth/register",
            json={
                "email": "me-test@example.com",
                "password": "securepass123",
                "confirmPassword": "securepass123",
            },
        )
        # Call /me — httpx preserves cookies across requests
        response = await async_client.get("/api/auth/me")
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "me-test@example.com"
        assert data["balance"] == 0
        assert data["role"] == "user"
        assert "user_id" in data

    @pytest.mark.asyncio
    async def test_me_401_without_cookie(self, async_client: AsyncClient):
        response = await async_client.get("/api/auth/me")
        assert response.status_code == 401
        data = response.json()
        assert data["code"] == "UNAUTHORIZED"

    @pytest.mark.asyncio
    async def test_me_401_with_invalid_cookie(self, async_client: AsyncClient):
        # Send an invalid cookie via explicit header (ASGI transport bypasses cookie jar)
        response = await async_client.get(
            "/api/auth/me",
            headers={"Cookie": "high_api_session=invalid-token-value"},
        )
        assert response.status_code == 401
        assert response.json()["code"] == "INVALID_TOKEN"


class TestLogoutEndpoint:
    @pytest.mark.asyncio
    async def test_logout_200(self, async_client: AsyncClient):
        # Register to get a session
        await async_client.post(
            "/api/auth/register",
            json={
                "email": "logout-test@example.com",
                "password": "securepass123",
                "confirmPassword": "securepass123",
            },
        )
        # Logout
        response = await async_client.post("/api/auth/logout")
        assert response.status_code == 200
        assert response.json() == {"message": "已退出登录"}

        # After logout, /me should return 401
        me_response = await async_client.get("/api/auth/me")
        assert me_response.status_code == 401


class TestChangePasswordEndpoint:
    @pytest.mark.asyncio
    async def test_change_password_200(self, async_client: AsyncClient):
        # Register & login
        await async_client.post(
            "/api/auth/register",
            json={
                "email": "cpe@example.com",
                "password": "oldpass123",
                "confirmPassword": "oldpass123",
            },
        )
        response = await async_client.post(
            "/api/auth/change-password",
            json={
                "currentPassword": "oldpass123",
                "newPassword": "newpass456",
                "confirmNewPassword": "newpass456",
            },
        )
        assert response.status_code == 200
        assert response.json() == {"message": "密码已修改"}

        # Verify old password no longer works for login
        login_response = await async_client.post(
            "/api/auth/login",
            json={"email": "cpe@example.com", "password": "oldpass123"},
        )
        assert login_response.status_code == 401

        # New password works
        login_response2 = await async_client.post(
            "/api/auth/login",
            json={"email": "cpe@example.com", "password": "newpass456"},
        )
        assert login_response2.status_code == 200

    @pytest.mark.asyncio
    async def test_change_password_400_wrong_current(self, async_client: AsyncClient):
        await async_client.post(
            "/api/auth/register",
            json={
                "email": "cpe2@example.com",
                "password": "oldpass123",
                "confirmPassword": "oldpass123",
            },
        )
        response = await async_client.post(
            "/api/auth/change-password",
            json={
                "currentPassword": "wrongpassword",
                "newPassword": "newpass456",
                "confirmNewPassword": "newpass456",
            },
        )
        assert response.status_code == 400
        assert response.json()["code"] == "WRONG_PASSWORD"

    @pytest.mark.asyncio
    async def test_change_password_401_unauthenticated(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/auth/change-password",
            json={
                "currentPassword": "oldpass123",
                "newPassword": "newpass456",
                "confirmNewPassword": "newpass456",
            },
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_change_password_422_mismatch(self, async_client: AsyncClient):
        await async_client.post(
            "/api/auth/register",
            json={
                "email": "cpe3@example.com",
                "password": "oldpass123",
                "confirmPassword": "oldpass123",
            },
        )
        response = await async_client.post(
            "/api/auth/change-password",
            json={
                "currentPassword": "oldpass123",
                "newPassword": "newpass456",
                "confirmNewPassword": "different",
            },
        )
        assert response.status_code == 422
        assert "不一致" in response.json()["error"]


class TestForgotPasswordEndpoint:
    @pytest.mark.asyncio
    async def test_forgot_password_200_existing_email(self, async_client: AsyncClient):
        await async_client.post(
            "/api/auth/register",
            json={
                "email": "fpe@example.com",
                "password": "securepass123",
                "confirmPassword": "securepass123",
            },
        )
        response = await async_client.post(
            "/api/auth/forgot-password",
            json={"email": "fpe@example.com"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "token" in data  # Dev mode returns token
        assert data["token"] is not None

    @pytest.mark.asyncio
    async def test_forgot_password_200_unknown_email(self, async_client: AsyncClient):
        """Should still return 200 to prevent email enumeration."""
        response = await async_client.post(
            "/api/auth/forgot-password",
            json={"email": "nobody@example.com"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "token" not in data


class TestResetPasswordEndpoint:
    @pytest.mark.asyncio
    async def test_reset_password_200(self, async_client: AsyncClient):
        # Register
        await async_client.post(
            "/api/auth/register",
            json={
                "email": "rpe@example.com",
                "password": "oldpass123",
                "confirmPassword": "oldpass123",
            },
        )
        # Get reset token
        fp_response = await async_client.post(
            "/api/auth/forgot-password",
            json={"email": "rpe@example.com"},
        )
        token = fp_response.json()["token"]

        # Reset password
        response = await async_client.post(
            "/api/auth/reset-password",
            json={
                "token": token,
                "newPassword": "newsecure456",
                "confirmNewPassword": "newsecure456",
            },
        )
        assert response.status_code == 200
        assert response.json() == {"message": "密码已重置，请使用新密码登录"}

        # Verify new password works
        login_response = await async_client.post(
            "/api/auth/login",
            json={"email": "rpe@example.com", "password": "newsecure456"},
        )
        assert login_response.status_code == 200

    @pytest.mark.asyncio
    async def test_reset_password_400_invalid_token(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/auth/reset-password",
            json={
                "token": "invalid-token",
                "newPassword": "newsecure456",
                "confirmNewPassword": "newsecure456",
            },
        )
        assert response.status_code == 400
        assert response.json()["code"] == "INVALID_RESET_TOKEN"


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_health_ok(self, async_client: AsyncClient):
        response = await async_client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
