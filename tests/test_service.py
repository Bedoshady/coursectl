"""Tests for AccountService: sign-in storage, key issuance, revocation."""

from __future__ import annotations

import httpx
import pytest
import respx

from coursectl.config import Settings
from coursectl.errors import InvalidCredentials, RegistrationClosed, Unauthorized
from coursectl.service import AccountService

SITE = "https://moodle.test"
PASSWORD = "super-secret-passphrase"
TOKEN = "moodle-token-abc123"

SITE_INFO = {
    "sitename": "eClass",
    "username": "abc123",
    "fullname": "Doe, Jane",
    "userid": 42,
    "useremail": "abc123@yorku.ca",
    "release": "5.0.1",
    "functions": [],
}


def make_settings(tmp_path, **overrides) -> Settings:
    return Settings(
        site_url=SITE,
        database_path=tmp_path / "coursectl.db",
        secret_key_file=tmp_path / "fernet.key",
        download_dir=tmp_path / "downloads",
        **overrides,
    )


def mock_upstream(token: str = TOKEN, token_json: dict | None = None):
    respx.post(url__regex=r".*/login/token\.php").mock(
        return_value=httpx.Response(
            200, json=token_json or {"token": token, "privatetoken": "priv-1"}
        )
    )
    respx.post(url__regex=r".*/webservice/rest/server\.php").mock(
        return_value=httpx.Response(200, json=SITE_INFO)
    )


@respx.mock
async def test_login_stores_token_and_returns_key(tmp_path):
    mock_upstream()
    service = AccountService(make_settings(tmp_path))

    account, api_key, trace = await service.login("abc123", PASSWORD)

    assert api_key.startswith("ck_")
    assert account.username == "abc123"
    assert account.display_name == "Doe, Jane"
    assert account.moodle_user_id == 42

    # The raw Moodle token must be recoverable, but never in plaintext on disk.
    stored = service.store.get_account_by_username("abc123")
    assert stored is not None
    assert service.secrets.decrypt(stored.token_enc) == TOKEN
    assert TOKEN not in stored.token_enc
    assert service.secrets.decrypt(stored.private_token_enc) == "priv-1"


@respx.mock
async def test_password_is_never_persisted(tmp_path):
    mock_upstream()
    settings = make_settings(tmp_path)
    service = AccountService(settings)

    await service.login("abc123", PASSWORD)
    service.store.log_event("login", detail="strategy=password")

    blob = settings.database_path.read_bytes()
    assert PASSWORD.encode() not in blob
    assert b"super-secret" not in blob


@respx.mock
async def test_resolve_round_trip(tmp_path):
    mock_upstream()
    service = AccountService(make_settings(tmp_path))
    _, api_key, _ = await service.login("abc123", PASSWORD)

    ctx = service.resolve(api_key)
    assert ctx.username == "abc123"
    assert ctx.account.moodle_user_id == 42

    with pytest.raises(Unauthorized):
        service.resolve("ck_not-a-real-key")


@respx.mock
async def test_relogin_rotates_and_invalidates_old_key(tmp_path):
    mock_upstream()
    service = AccountService(make_settings(tmp_path))

    _, first_key, _ = await service.login("abc123", PASSWORD)
    _, second_key, _ = await service.login("abc123", PASSWORD)

    assert first_key != second_key
    with pytest.raises(Unauthorized):
        service.resolve(first_key)
    assert service.resolve(second_key).username == "abc123"
    assert len(service.list_accounts()) == 1


@respx.mock
async def test_revoke_kills_key_and_token(tmp_path):
    mock_upstream()
    service = AccountService(make_settings(tmp_path))
    _, api_key, _ = await service.login("abc123", PASSWORD)

    assert service.revoke("abc123") is True
    with pytest.raises(Unauthorized):
        service.resolve(api_key)

    stored = service.store.get_account_by_username("abc123")
    assert stored is not None and stored.token_enc == ""
    assert service.revoke("abc123") is False


@respx.mock
async def test_invalid_credentials_propagate(tmp_path):
    mock_upstream(token_json={"error": "Acceso inválido.", "errorcode": "invalidlogin"})
    service = AccountService(make_settings(tmp_path))

    with pytest.raises(InvalidCredentials):
        await service.login("abc123", "wrong", strategy="password")


@respx.mock
async def test_closed_registration_requires_secret(tmp_path):
    mock_upstream()
    settings = make_settings(
        tmp_path, allow_open_registration=False, registration_secret="sesame"
    )
    service = AccountService(settings)

    with pytest.raises(RegistrationClosed):
        await service.login("abc123", PASSWORD)

    with pytest.raises(RegistrationClosed):
        await service.login("abc123", PASSWORD, registration_secret="wrong")

    account, api_key, _ = await service.login("abc123", PASSWORD, registration_secret="sesame")
    assert account.username == "abc123"
    assert api_key.startswith("ck_")


@respx.mock
async def test_token_rejected_immediately_is_reported(tmp_path):
    respx.post(url__regex=r".*/login/token\.php").mock(
        return_value=httpx.Response(200, json={"token": "bogus"})
    )
    respx.post(url__regex=r".*/webservice/rest/server\.php").mock(
        return_value=httpx.Response(
            200, json={"exception": "moodle_exception", "errorcode": "invalidtoken",
                       "message": "Invalid token"}
        )
    )
    service = AccountService(make_settings(tmp_path))

    from coursectl.errors import SessionExpired

    with pytest.raises(SessionExpired):
        await service.login("abc123", PASSWORD)


async def test_empty_credentials_rejected(tmp_path):
    service = AccountService(make_settings(tmp_path))
    with pytest.raises(Unauthorized):
        await service.login("", "")


@respx.mock
async def test_login_via_cookies_provisions_from_session(tmp_path):
    """The MFA path: cookies in, account provisioned from Moodle's own identity."""
    import base64

    payload = base64.b64encode(b"sig:::cookie-token:::cookie-priv").decode()
    respx.get(url__regex=r".*/admin/tool/mobile/launch\.php\?.*").mock(
        return_value=httpx.Response(303, headers={"location": f"moodlemobile://token={payload}"})
    )
    respx.post(url__regex=r".*/webservice/rest/server\.php").mock(
        return_value=httpx.Response(200, json=SITE_INFO)
    )
    service = AccountService(make_settings(tmp_path))

    account, api_key, _ = await service.login_via_cookies({"MoodleSession": "live-session"})

    # Username comes from Moodle, not from the caller.
    assert account.username == "abc123"
    assert account.display_name == "Doe, Jane"
    assert api_key.startswith("ck_")
    stored = service.store.get_account_by_username("abc123")
    assert stored is not None
    assert service.secrets.decrypt(stored.token_enc) == "cookie-token"
    assert service.resolve(api_key).username == "abc123"
