"""Tests for the SSO login flow, driven entirely by mocked HTTP."""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from eclass_api.auth import (
    InvalidCredentials,
    LoginTrace,
    acquire_tokens,
    mobile_sso_login,
    parse_token_redirect,
)
from eclass_api.config import Settings
from eclass_api.errors import LoginFlowError, TokenUnavailable

LOGIN_HANDLER_HTML = """
<html><body>
  <h1>Loading Session Information</h1>
  <form name="form1" action="/idp/profile/SAML2/Redirect/SSO?execution=e1s1" method="post">
    <input type="hidden" name="csrf_token" value="csrf-value-123" />
    <input name="shib_idp_ls_supported" type="hidden" />
    <input name="shib_idp_ls_success.shib_idp_session_ss" type="hidden" value="false" />
    <input type="submit" value="Continue" />
  </form>
</body></html>
"""

PPY_FORM_HTML = """
<html><head><title>Passport York Login</title></head><body>
  <form name="loginform" method="POST" action="/ppylogin/ppylogin">
    <input type="text" id="mli" maxlength="9" name="mli" />
    <input type="password" id="password" maxlength="128" name="password" />
    <input type="submit" class="btn" name="dologin" value="Login" />
    <input type="checkbox" id="passchangewanted" name="passchangewanted" value="on" />
    <input type="hidden" name="__albform__" value="eJxzrwoW1lf+Ep59Zw==" />
    <input type="hidden" name="__albstate__" value="eJzjYAx++OxXttuXyA==" />
    <input type="hidden" name="__pybpp__" value="iWo2qMtP5fjZRK5XPB/MMSuig20w" />
  </form>
  <div class="alert alert-warning">compatibility warning</div>
  <div class="alert alert-info">maintenance window</div>
</body></html>
"""

PPY_ERROR_HTML = """
<html><body>
  <div class="alert alert-danger">Passport York: User ID or password is invalid.</div>
  <form name="loginform" method="POST" action="/ppylogin/ppylogin">
    <input type="text" id="mli" name="mli" />
    <input type="password" id="password" name="password" />
    <input type="submit" name="dologin" value="Login" />
  </form>
</body></html>
"""


def make_settings() -> Settings:
    return Settings(
        site_url="https://eclass.test",
        passport_host="passportyork.test",
        database_path=":memory:",  # type: ignore[arg-type]
        secret_key_file="memory.key",  # type: ignore[arg-type]
        download_dir="downloads",  # type: ignore[arg-type]
    )


def token_redirect(token: str = "the-token", private: str = "the-private") -> str:
    payload = f"sigvalue:::{token}:::{private}"
    encoded = base64.b64encode(payload.encode()).decode()
    return f"moodlemobile://token={encoded}"


# ---------------------------------------------------------------------------
# parse_token_redirect
# ---------------------------------------------------------------------------
def test_parse_token_redirect_splits_triple_colon():
    bundle = parse_token_redirect(token_redirect("abc", "priv"))
    assert bundle.token == "abc"
    assert bundle.private_token == "priv"
    assert bundle.signature == "sigvalue"
    assert "redacted" in repr(bundle)


def test_parse_token_redirect_handles_base64_with_plus_and_slash():
    # '??>' encodes to 'Pz8+' and '///' to 'Ly8v', both legal inside base64.
    payload = "s???:::tok+en/with+slashes==="
    encoded = base64.b64encode(payload.encode()).decode()
    bundle = parse_token_redirect(f"moodlemobile://token={encoded}")
    assert bundle.token == "tok+en/with+slashes==="


def test_parse_token_redirect_without_private_token():
    encoded = base64.b64encode(b"sig:::only-token").decode()
    bundle = parse_token_redirect(f"moodlemobile://token={encoded}")
    assert bundle.token == "only-token"
    assert bundle.private_token is None


def test_parse_token_redirect_rejects_wrong_scheme():
    with pytest.raises(TokenUnavailable):
        parse_token_redirect("https://eclass.test/somewhere")


def test_parse_token_redirect_rejects_garbage():
    with pytest.raises(TokenUnavailable):
        parse_token_redirect("moodlemobile://token=!!!not-base64!!!")


# ---------------------------------------------------------------------------
# full mobile_sso_login walk
# ---------------------------------------------------------------------------
@respx.mock
async def test_mobile_sso_login_completes_full_chain():
    settings = make_settings()
    final = token_redirect("real-token", "real-private")

    respx.get(url__regex=r"^https://eclass\.test/admin/tool/mobile/launch\.php\?.*").mock(
        return_value=httpx.Response(
            303, headers={"location": "https://shib.test/idp/profile/SAML2/Redirect/SSO?SAMLRequest=x"}
        )
    )
    respx.get("https://shib.test/idp/profile/SAML2/Redirect/SSO?SAMLRequest=x").mock(
        return_value=httpx.Response(
            302, headers={"location": "/idp/profile/SAML2/Redirect/SSO?execution=e1s1"}
        )
    )
    respx.get(url__regex=r".*execution=e1s1$").mock(
        return_value=httpx.Response(200, text=LOGIN_HANDLER_HTML)
    )
    login_handler_post = respx.post(url__regex=r".*execution=e1s1$").mock(
        return_value=httpx.Response(
            302, headers={"location": "/idp/profile/SAML2/Redirect/SSO?execution=e1s2"}
        )
    )
    respx.get(url__regex=r".*execution=e1s2$").mock(
        return_value=httpx.Response(
            302, headers={"location": "/idp/Authn/RemoteUser?conversation=e1s2"}
        )
    )
    respx.get(url__regex=r".*Authn/RemoteUser.*").mock(
        return_value=httpx.Response(
            302, headers={"location": "https://passportyork.test/ppylogin/ppylogin"}
        )
    )
    respx.get("https://passportyork.test/ppylogin/ppylogin").mock(
        return_value=httpx.Response(200, text=PPY_FORM_HTML)
    )
    credentials_post = respx.post("https://passportyork.test/ppylogin/ppylogin").mock(
        return_value=httpx.Response(
            302, headers={"location": "https://shib.test/idp/profile/SAML2/POST/SSO"}
        )
    )
    respx.get(url__regex=r".*SAML2/POST/SSO.*").mock(
        return_value=httpx.Response(
            302, headers={"location": "https://eclass.test/Shibboleth.sso/SAML2/POST"}
        )
    )
    respx.get("https://eclass.test/Shibboleth.sso/SAML2/POST").mock(
        return_value=httpx.Response(303, headers={"location": final})
    )

    trace = LoginTrace()
    bundle = await mobile_sso_login("abc123", "hunter2", settings, trace=trace)

    assert bundle.token == "real-token"
    assert bundle.private_token == "real-private"
    assert bundle.strategy == "mobile_sso"

    # The CSRF-protected interstitial was submitted with its token.
    assert login_handler_post.called
    body = login_handler_post.calls[0].request.content.decode()
    assert "csrf_token=csrf-value-123" in body

    # Credentials went to Passport York, with the anti-bot fields preserved.
    assert credentials_post.called
    sent = credentials_post.calls[0].request.content.decode()
    assert "mli=abc123" in sent
    assert "password=hunter2" in sent
    assert "__albform__" in sent
    assert "__pybpp__" in sent
    assert "dologin=Login" in sent
    # Never tick "change my Passport York password" as a side effect of logging in.
    assert "passchangewanted" not in sent

    assert any("Moodle returned the token" in h for h in trace.hops)


@respx.mock
async def test_mobile_sso_login_rejects_bad_password():
    settings = make_settings()

    respx.get(url__regex=r"^https://eclass\.test/admin/tool/mobile/launch\.php\?.*").mock(
        return_value=httpx.Response(
            303, headers={"location": "https://shib.test/idp/profile/SAML2/Redirect/SSO?SAMLRequest=x"}
        )
    )
    respx.get(url__regex=r".*SAMLRequest=x$").mock(
        return_value=httpx.Response(
            302, headers={"location": "/idp/profile/SAML2/Redirect/SSO?execution=e1s1"}
        )
    )
    respx.get(url__regex=r".*execution=e1s1$").mock(
        return_value=httpx.Response(200, text=LOGIN_HANDLER_HTML)
    )
    respx.post(url__regex=r".*execution=e1s1$").mock(
        return_value=httpx.Response(
            302, headers={"location": "/idp/Authn/RemoteUser?conversation=e1s2"}
        )
    )
    respx.get(url__regex=r".*Authn/RemoteUser.*").mock(
        return_value=httpx.Response(
            302, headers={"location": "https://passportyork.test/ppylogin/ppylogin"}
        )
    )
    respx.get("https://passportyork.test/ppylogin/ppylogin").mock(
        return_value=httpx.Response(200, text=PPY_FORM_HTML)
    )
    respx.post("https://passportyork.test/ppylogin/ppylogin").mock(
        return_value=httpx.Response(200, text=PPY_ERROR_HTML)
    )

    with pytest.raises(InvalidCredentials) as excinfo:
        await mobile_sso_login("abc123", "wrong", settings)

    assert "invalid" in excinfo.value.message.lower()


@respx.mock
async def test_mobile_sso_login_flags_unknown_page():
    settings = make_settings()
    respx.get(url__regex=r"^https://eclass\.test/admin/tool/mobile/launch\.php\?.*").mock(
        return_value=httpx.Response(
            303, headers={"location": "https://shib.test/idp/profile/SAML2/Redirect/SSO?SAMLRequest=x"}
        )
    )
    respx.get(url__regex=r".*SAMLRequest=x$").mock(
        return_value=httpx.Response(
            200,
            text="<html><title>Terms of Use</title><p>You must accept the acceptable use policy.</p></html>",
        )
    )

    with pytest.raises(LoginFlowError) as excinfo:
        await mobile_sso_login("abc123", "pw", settings)

    assert "does not recognise" in excinfo.value.message
    assert excinfo.value.detail["title"] == "Terms of Use"


@respx.mock
async def test_mobile_sso_login_reports_mfa_not_bad_password():
    """The regression: a Duo page after a *correct* password must not read as 'rejected'."""
    from eclass_api.errors import MfaRequired

    settings = make_settings()
    respx.get(url__regex=r"^https://eclass\.test/admin/tool/mobile/launch\.php\?.*").mock(
        return_value=httpx.Response(
            303, headers={"location": "https://shib.test/idp/profile/SAML2/Redirect/SSO?SAMLRequest=x"}
        )
    )
    respx.get(url__regex=r".*SAMLRequest=x$").mock(
        return_value=httpx.Response(
            302, headers={"location": "/idp/profile/SAML2/Redirect/SSO?execution=e1s1"}
        )
    )
    respx.get(url__regex=r".*execution=e1s1$").mock(
        return_value=httpx.Response(200, text=LOGIN_HANDLER_HTML)
    )
    respx.post(url__regex=r".*execution=e1s1$").mock(
        return_value=httpx.Response(
            302, headers={"location": "/idp/Authn/RemoteUser?conversation=e1s2"}
        )
    )
    respx.get(url__regex=r".*Authn/RemoteUser.*").mock(
        return_value=httpx.Response(
            302, headers={"location": "https://passportyork.test/ppylogin/ppylogin"}
        )
    )
    respx.get("https://passportyork.test/ppylogin/ppylogin").mock(
        return_value=httpx.Response(200, text=PPY_FORM_HTML)
    )
    respx.post("https://passportyork.test/ppylogin/ppylogin").mock(
        return_value=httpx.Response(
            200,
            text=(
                "<html><title>Duo Security</title>"
                "<p>Check your phone. Send me a push to approve this login.</p></html>"
            ),
        )
    )

    with pytest.raises(MfaRequired) as excinfo:
        await mobile_sso_login("abc123", "correct-password", settings)

    # Crucially it is NOT InvalidCredentials.
    assert not isinstance(excinfo.value, InvalidCredentials)
    assert excinfo.value.detail["stage"] == "after_password"
    assert "--browser" in excinfo.value.message


@respx.mock
async def test_wrong_password_still_reports_invalid_credentials():
    settings = make_settings()
    respx.get(url__regex=r"^https://eclass\.test/admin/tool/mobile/launch\.php\?.*").mock(
        return_value=httpx.Response(
            303, headers={"location": "https://shib.test/idp/profile/SAML2/Redirect/SSO?SAMLRequest=x"}
        )
    )
    respx.get(url__regex=r".*SAMLRequest=x$").mock(
        return_value=httpx.Response(
            302, headers={"location": "/idp/profile/SAML2/Redirect/SSO?execution=e1s1"}
        )
    )
    respx.get(url__regex=r".*execution=e1s1$").mock(
        return_value=httpx.Response(200, text=LOGIN_HANDLER_HTML)
    )
    respx.post(url__regex=r".*execution=e1s1$").mock(
        return_value=httpx.Response(
            302, headers={"location": "/idp/Authn/RemoteUser?conversation=e1s2"}
        )
    )
    respx.get(url__regex=r".*Authn/RemoteUser.*").mock(
        return_value=httpx.Response(
            302, headers={"location": "https://passportyork.test/ppylogin/ppylogin"}
        )
    )
    respx.get("https://passportyork.test/ppylogin/ppylogin").mock(
        return_value=httpx.Response(200, text=PPY_FORM_HTML)
    )
    respx.post("https://passportyork.test/ppylogin/ppylogin").mock(
        return_value=httpx.Response(200, text=PPY_ERROR_HTML)
    )

    with pytest.raises(InvalidCredentials):
        await mobile_sso_login("abc123", "wrong", settings)


# ---------------------------------------------------------------------------
# session-cookie path (the MFA escape hatch)
# ---------------------------------------------------------------------------
def test_parse_cookie_header_formats():
    from eclass_api.auth import parse_cookie_header

    expected = {"MoodleSession": "abc", "MDL_SSP_SessID": "def"}
    assert parse_cookie_header("MoodleSession=abc; MDL_SSP_SessID=def") == expected
    assert parse_cookie_header('{"MoodleSession": "abc", "MDL_SSP_SessID": "def"}') == expected
    assert parse_cookie_header("MoodleSession=abc\nMDL_SSP_SessID=def") == expected
    assert parse_cookie_header("   ") == {}
    assert parse_cookie_header("") == {}


def test_parse_cookie_header_handles_devtools_tabs():
    from eclass_api.auth import parse_cookie_header

    pasted = "MoodleSession\tf8f5c9d0e1a2b3\t.yorku.ca\t/ \tSession\tMedium"
    parsed = parse_cookie_header(pasted)
    assert parsed.get("MoodleSession") == "f8f5c9d0e1a2b3"


@respx.mock
async def test_token_from_cookies_mints_token():
    from eclass_api.auth import token_from_cookies

    settings = make_settings()
    final = token_redirect("cookie-token", "cookie-priv")
    route = respx.get(url__regex=r"^https://eclass\.test/admin/tool/mobile/launch\.php\?.*").mock(
        return_value=httpx.Response(303, headers={"location": final})
    )

    bundle = await token_from_cookies({"MoodleSession": "abc"}, settings)

    assert bundle.token == "cookie-token"
    # The session cookie must actually be sent to Moodle.
    sent = route.calls[0].request.headers.get("cookie", "")
    assert "MoodleSession=abc" in sent


@respx.mock
async def test_token_from_cookies_rejects_unauthenticated_session():
    from eclass_api.auth import token_from_cookies
    from eclass_api.errors import SessionNotAuthenticated

    settings = make_settings()
    respx.get(url__regex=r"^https://eclass\.test/admin/tool/mobile/launch\.php\?.*").mock(
        return_value=httpx.Response(
            303, headers={"location": "https://shib.yorku.ca/idp/profile/SAML2/Redirect/SSO?x=1"}
        )
    )

    with pytest.raises(SessionNotAuthenticated) as excinfo:
        await token_from_cookies({"MoodleSession": "stale"}, settings)
    assert "not an authenticated" in excinfo.value.message


async def test_token_from_cookies_requires_cookies():
    from eclass_api.auth import token_from_cookies
    from eclass_api.errors import SessionNotAuthenticated

    with pytest.raises(SessionNotAuthenticated):
        await token_from_cookies({}, make_settings())
@respx.mock
async def test_auto_strategy_prefers_password_endpoint():
    settings = make_settings()
    respx.post(url__regex=r".*/login/token\.php").mock(
        return_value=httpx.Response(200, json={"token": "pw-token", "privatetoken": "pw-priv"})
    )

    bundle = await acquire_tokens("abc123", "pw", settings, strategy="auto")
    assert bundle.token == "pw-token"
    assert bundle.strategy == "password"


@respx.mock
async def test_auto_strategy_falls_back_to_sso(monkeypatch):
    settings = make_settings()
    respx.post(url__regex=r".*/login/token\.php").mock(
        return_value=httpx.Response(
            200, json={"error": "Acceso inválido.", "errorcode": "invalidlogin"}
        )
    )

    async def fake_sso(*args, **kwargs):
        from eclass_api.auth import TokenBundle

        return TokenBundle(token="sso-token", strategy="mobile_sso")

    monkeypatch.setattr("eclass_api.auth.mobile_sso_login", fake_sso)

    trace = LoginTrace()
    bundle = await acquire_tokens("abc123", "pw", settings, strategy="auto", trace=trace)
    assert bundle.token == "sso-token"
    assert any("switching to SSO" in h for h in trace.hops)


@respx.mock
async def test_password_strategy_reports_invalid_login():
    settings = make_settings()
    respx.post(url__regex=r".*/login/token\.php").mock(
        return_value=httpx.Response(
            200, json={"error": "Acceso inválido.", "errorcode": "invalidlogin"}
        )
    )
    with pytest.raises(InvalidCredentials):
        await acquire_tokens("abc123", "bad", settings, strategy="password")


@respx.mock
async def test_password_strategy_handles_non_json():
    settings = make_settings()
    respx.post(url__regex=r".*/login/token\.php").mock(
        return_value=httpx.Response(200, text="<html>maintenance</html>")
    )
    from eclass_api.errors import UpstreamError

    with pytest.raises(UpstreamError):
        await acquire_tokens("abc123", "pw", settings, strategy="password")
