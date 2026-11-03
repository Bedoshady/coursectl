"""Acquire a Moodle web-service token on the server, on the user's behalf.

Two strategies
--------------
``password``
    POST straight to ``/login/token.php``. Works only on Moodle instances that
    keep a password-capable auth plugin enabled. One request, very cheap.

``mobile_sso``
    Drive the real single-sign-on flow that the official Moodle mobile app uses:

        GET  launch.php?service=…&passport=…&urlscheme=moodlemobile
          → 303 Shibboleth SAML2/Redirect
          → 302 ?execution=e1s1            (LoginHandler interstitial)
          → POST _eventId_proceed          (CSRF-protected form)
          → 302 ?execution=e1s2
          → 302 Authn/RemoteUser
          → 200 Passport York credential form
          → POST mli/password/__alb*
          → 302 back through Shibboleth → eClass
          → 303 moodlemobile://token=<base64>

    The base64 payload decodes to ``signature:::token:::privatetoken``.

Both end with a genuine Moodle token, so everything afterwards is ordinary
documented Web Services — no scraping, no cookie replay, no HTML parsing.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import unquote, urljoin

import httpx

from .config import Settings
from .errors import (
    InvalidCredentials,
    LoginFlowError,
    MfaRequired,
    SessionNotAuthenticated,
    TokenUnavailable,
    UpstreamError,
)
from .htmlforms import Form, looks_like_credential_form, parse_forms, pick_progression_form

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-CA,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}

USERNAME_FIELD_NAMES = ("mli", "username", "user", "j_username", "login", "userid", "user_id")
PASSWORD_FIELD_NAMES = ("password", "pass", "j_password", "pwd")

# Sign-in must be strictly read-only: never submit anything that would mutate
# the account. Passport York's login form carries a "change my password"
# checkbox (`passchangewanted`), and submitting it would change the user's
# password as a side effect of logging in.
NEVER_SUBMIT = frozenset(
    {
        "passchangewanted",
        "changepassword",
        "change_password",
        "newpassword",
        "new_password",
        "newpassword1",
        "newpassword2",
        "confirmpassword",
        "resetpassword",
    }
)


@dataclass(slots=True)
class TokenBundle:
    """What Moodle hands back once authentication succeeds."""

    token: str
    private_token: str | None = None
    signature: str | None = None
    strategy: str = "mobile_sso"

    def __repr__(self) -> str:  # never leak tokens into logs
        return f"TokenBundle(strategy={self.strategy!r}, token=<redacted>)"


@dataclass
class LoginTrace:
    """Human-readable breadcrumb trail, useful when York changes their login page."""

    hops: list[str] = field(default_factory=list)

    def add(self, message: str) -> None:
        self.hops.append(message)

    def __str__(self) -> str:
        return "\n".join(f"  {i + 1}. {h}" for i, h in enumerate(self.hops))


def _sanitize_url(url: str, limit: int = 120) -> str:
    """Drop query strings that carry SAML payloads/CSRF values before logging."""
    base, _, query = url.partition("?")
    if not query:
        return base[:limit]
    keys = [seg.split("=", 1)[0] for seg in query.split("&") if seg]
    shown = ",".join(keys[:4])
    return f"{base}?{shown}{'…' if len(keys) > 4 else ''}"[:limit]


def parse_token_redirect(location: str, urlscheme: str = "moodlemobile") -> TokenBundle:
    """Decode ``<scheme>://token=<base64>`` into a :class:`TokenBundle`.

    The payload is split on ``:::` into ``signature``, ``token`` and
    ``privatetoken``. Older Moodle releases omit the private token.
    """
    prefix = f"{urlscheme}://"
    if not location.startswith(prefix):
        raise TokenUnavailable(
            "Moodle did not redirect to the expected app URL.", detail={"location": location[:80]}
        )

    payload = location[len(prefix) :]
    key, _, value = payload.partition("=")
    if key != "token" or not value:
        raise TokenUnavailable(
            "Moodle redirected without a token parameter.", detail={"payload": payload[:80]}
        )

    # Percent-decoding only: a literal '+' is valid base64 here, not a space.
    encoded = unquote(value)
    try:
        decoded = base64.b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8", "replace")
    except (binascii.Error, ValueError) as exc:
        raise TokenUnavailable("Token payload was not valid base64.") from exc

    parts = decoded.split(":::")
    if len(parts) < 2:
        raise TokenUnavailable("Token payload had an unexpected shape.", detail={"parts": len(parts)})

    signature = parts[0] or None
    token = parts[1]
    private_token = parts[2] if len(parts) > 2 and parts[2] else None
    if not token:
        raise TokenUnavailable("Token payload contained an empty token.")
    return TokenBundle(token=token, private_token=private_token, signature=signature)


def _visible_text(html: str, limit: int = 400) -> str:
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _extract_error_message(html: str) -> str:
    """Best-effort pull of a human error message out of a failed login page."""
    for pattern in (
        r'(?is)<[^>]*class="[^"]*(?:alert-danger|alert-error|text-danger|error-message|errorbox)[^"]*"[^>]*>(.*?)</',
        r'(?is)<[^>]*id="(?:error|errorbox|loginerror)"[^>]*>(.*?)</',
        r"(?is)<[^>]*class=\"[^\"]*error[^\"]*\"[^>]*>(.*?)</",
    ):
        match = re.search(pattern, html)
        if match:
            cleaned = re.sub(r"(?s)<[^>]+>", " ", match.group(1))
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if cleaned:
                return cleaned[:300]

    for phrase in (
        r"User ID or password (?:is )?(?:invalid|incorrect)[^.<]*",
        r"(?:invalid|incorrect) (?:user ?id|username|password)[^.<]*",
        r"Passport York [^.]*?(?:invalid|incorrect|locked|failed)[^.]*",
        r"[^.]*?(?:account|user ?id)\s+(?:is\s+)?locked[^.]*",
        r"Authentication failed[^.]*",
    ):
        match = re.search(phrase, html, re.IGNORECASE)
        if match:
            return re.sub(r"\s+", " ", match.group(0)).strip()[:300]

    return "Passport York rejected the sign-in attempt."


FORCED_CHANGE_PHRASES = (
    "password has expired",
    "password expired",
    "must be changed",
    "must change your password",
    "change your password",
    "password change required",
    "new password",
)

# Strings that betray a second-factor / federated-identity challenge. Matched
# case-insensitively against the visible text and the page URL.
MFA_HINTS = (
    "duo",
    "duosecurity",
    "two-factor",
    "two factor",
    "2fa",
    "mfa",
    "multi-factor",
    "multifactor",
    "second factor",
    "authenticator",
    "passcode",
    "send me a push",
    "push notification",
    "okta",
    "webauthn",
    "security key",
    "verification code",
    "one-time code",
    "verify your identity",
    "it's you",
    "its you",
)

MFA_HOST_HINTS = ("duosecurity.com", "duo.com", "okta.com", "login.microsoftonline", "microsoftonline")


def _looks_like_mfa(html: str, url: str = "") -> bool:
    """True if this page looks like a second-factor challenge."""
    lowered_url = (url or "").lower()
    if any(hint in lowered_url for hint in MFA_HOST_HINTS):
        return True
    text = _visible_text(html, limit=4000).lower()
    return any(hint in text for hint in MFA_HINTS)


def _looks_like_password_change(form: Form, html: str) -> bool:
    """Tell a forced password-change page apart from a rejected login.

    Two password inputs (new + confirm) is the giveaway; failing that, look for
    the phrasing York uses when it forces a rotation.
    """
    if sum(1 for f in form.fields if f.is_password) >= 2:
        return True
    lowered = _visible_text(html, limit=2000).lower()
    return any(phrase in lowered for phrase in FORCED_CHANGE_PHRASES)


def _find_username_field(form: Form) -> str | None:
    for candidate in USERNAME_FIELD_NAMES:
        if form.has(candidate):
            return candidate
    return None


def _build_credential_payload(form: Form, username: str, password: str) -> dict[str, str]:
    payload = form.payload(skip_types=("button", "image", "checkbox"))
    user_field = _find_username_field(form)
    if user_field is None:
        raise LoginFlowError(
            "Could not find a username field on the sign-in form.",
            detail={"fields": [f.name for f in form.fields]},
        )

    pass_field = next(
        (f.name for f in form.fields if f.is_password),
        None,
    )
    if pass_field is None:
        for candidate in PASSWORD_FIELD_NAMES:
            if form.has(candidate):
                pass_field = candidate
                break
    if pass_field is None:
        raise LoginFlowError("Could not find a password field on the sign-in form.")

    payload[user_field] = username
    payload[pass_field] = password

    # Echo the submit button so servers that branch on it behave normally.
    for f in form.fields:
        if f.type.lower() == "submit" and f.name:
            payload.setdefault(f.name, f.value or "")
            break

    # Belt and braces: strip any account-mutating field regardless of how the
    # form was parsed.
    for name in list(payload):
        if name.strip().lower() in NEVER_SUBMIT:
            payload.pop(name, None)
    return payload


def _launch_url(settings: Settings, passport: str) -> str:
    return (
        f"{settings.site_url}{settings.launch_path}"
        f"?service={settings.service}&passport={passport}&urlscheme={settings.urlscheme}"
    )


async def mobile_sso_login(
    username: str,
    password: str,
    settings: Settings,
    *,
    trace: LoginTrace | None = None,
) -> TokenBundle:
    """Walk the Shibboleth + Passport York flow and return Moodle's token."""
    trace = trace if trace is not None else LoginTrace()
    passport = secrets.token_hex(16)
    url = _launch_url(settings, passport)
    submitted_credentials = False

    timeout = httpx.Timeout(settings.login_timeout, connect=15.0)
    async with httpx.AsyncClient(
        follow_redirects=False, timeout=timeout, headers=BASE_HEADERS
    ) as client:
        trace.add(f"GET {_sanitize_url(url)}")

        for _ in range(settings.max_redirects * 3):
            try:
                resp = await client.get(url)
            except httpx.HTTPError as exc:
                raise UpstreamError(f"Network error during sign-in: {exc}") from exc

            location = resp.headers.get("location")

            if resp.is_redirect and location:
                target = urljoin(str(resp.url), location)

                if target.startswith(f"{settings.urlscheme}://"):
                    trace.add(f"Moodle returned the token (HTTP {resp.status_code})")
                    return parse_token_redirect(target, settings.urlscheme)

                # Shibboleth bounces to its own host to re-drive the conversation.
                if "Shibboleth.sso" in target and "error" in target.lower():
                    raise LoginFlowError("Shibboleth reported an error after sign-in.")

                trace.add(f"{resp.status_code} → {_sanitize_url(target)}")
                url = target
                continue

            if resp.status_code >= 400:
                raise LoginFlowError(
                    f"Sign-in flow failed with HTTP {resp.status_code}.",
                    detail={"url": _sanitize_url(str(resp.url)), "body": _visible_text(resp.text, 200)},
                )

            html = resp.text
            forms = parse_forms(html, base_url=str(resp.url))

            credential_form = looks_like_credential_form(forms)
            if credential_form is not None:
                if submitted_credentials:
                    # We already sent the password and are looking at a password
                    # form again. Distinguish a rejected login from a forced
                    # password change (which is not an error we can fix).
                    if _looks_like_password_change(credential_form, html):
                        raise LoginFlowError(
                            "Passport York is asking for a password change. "
                            "Sign in with a browser once to complete it, then retry.",
                            detail={"url": _sanitize_url(str(resp.url)), "title": _page_title(html)},
                        )
                    raise InvalidCredentials(_extract_error_message(html))

                payload = _build_credential_payload(credential_form, username, password)
                action = credential_form.action or str(resp.url)
                trace.add(f"POST {_sanitize_url(action)} (credentials, {len(payload)} fields)")

                try:
                    resp = await client.post(
                        action,
                        data=payload,
                        headers={
                            "Referer": str(resp.url),
                            "Origin": f"https://{httpx.URL(action).host}",
                            "Content-Type": "application/x-www-form-urlencoded",
                        },
                    )
                except httpx.HTTPError as exc:
                    raise UpstreamError(f"Network error submitting credentials: {exc}") from exc

                submitted_credentials = True

                post_location = resp.headers.get("location")
                if resp.is_redirect and post_location:
                    target = urljoin(str(resp.url), post_location)
                    if target.startswith(f"{settings.urlscheme}://"):
                        return parse_token_redirect(target, settings.urlscheme)
                    trace.add(f"{resp.status_code} → {_sanitize_url(target)}")
                    url = target
                    continue

                # A 200 right after submitting credentials means we stayed put.
                # That is NOT necessarily a bad password: it may be a second
                # factor, which we must report honestly rather than call a
                # rejection.
                if resp.status_code == 200:
                    post_forms = parse_forms(resp.text, base_url=str(resp.url))
                    post_credential_form = looks_like_credential_form(post_forms)

                    if post_credential_form is None and _looks_like_mfa(resp.text, str(resp.url)):
                        raise MfaRequired(
                            "Passport York accepted the password but asked for a second "
                            "factor (Duo). Interactive MFA cannot be completed by a "
                            "headless client — use `coursectl login --browser` or "
                            "`coursectl login --cookie`.",
                            detail={
                                "stage": "after_password",
                                "url": _sanitize_url(str(resp.url)),
                                "title": _page_title(resp.text),
                                "text": _visible_text(resp.text, 300),
                            },
                        )

                    if post_credential_form is not None and _looks_like_password_change(
                        post_credential_form, resp.text
                    ):
                        raise LoginFlowError(
                            "Passport York is asking for a password change. "
                            "Sign in with a browser once to complete it, then retry.",
                            detail={"url": _sanitize_url(str(resp.url))},
                        )

                    if post_credential_form is not None:
                        raise InvalidCredentials(_extract_error_message(resp.text))

                    # Unknown page that is not an error: report it, do not guess.
                    raise LoginFlowError(
                        "Sign-in reached an unrecognised page instead of completing.",
                        detail={
                            "stage": "after_password",
                            "url": _sanitize_url(str(resp.url)),
                            "title": _page_title(resp.text),
                            "text": _visible_text(resp.text, 300),
                        },
                    )
                raise LoginFlowError(
                    f"Unexpected response after submitting credentials (HTTP {resp.status_code})."
                )

            # A second factor may also appear as a redirect chain that lands on
            # a page with a form we would otherwise auto-submit.
            if _looks_like_mfa(html, str(resp.url)):
                raise MfaRequired(
                    "York is requesting a second factor (Duo). Complete it with "
                    "`coursectl login --browser`, then reuse that session.",
                    detail={
                        "stage": "challenge",
                        "url": _sanitize_url(str(resp.url)),
                        "title": _page_title(html),
                    },
                )

            progression = pick_progression_form(forms)
            if progression is not None:
                action = progression.action or str(resp.url)
                payload = progression.payload()
                trace.add(
                    f"POST {_sanitize_url(action)} (sso step, fields: {','.join(sorted(payload))[:120]})"
                )
                try:
                    resp = await client.post(
                        action,
                        data=payload,
                        headers={
                            "Referer": str(resp.url),
                            "Origin": f"https://{httpx.URL(action).host}",
                            "Content-Type": "application/x-www-form-urlencoded",
                        },
                    )
                except httpx.HTTPError as exc:
                    raise UpstreamError(f"Network error during SSO step: {exc}") from exc

                next_location = resp.headers.get("location")
                if resp.is_redirect and next_location:
                    target = urljoin(str(resp.url), next_location)
                    if target.startswith(f"{settings.urlscheme}://"):
                        return parse_token_redirect(target, settings.urlscheme)
                    trace.add(f"{resp.status_code} → {_sanitize_url(target)}")
                    url = target
                    continue

                if resp.status_code == 200:
                    # Interstitial with a fresh form; loop and re-examine it.
                    url = str(resp.url)
                    continue
                raise LoginFlowError(
                    f"Unexpected response during SSO step (HTTP {resp.status_code}).",
                    detail={"url": _sanitize_url(str(resp.url))},
                )

            # A terminal page we do not understand: surface enough to debug it.
            raise LoginFlowError(
                "Sign-in flow reached a page this server does not recognise. "
                "York may have added an MFA or consent step.",
                detail={
                    "url": _sanitize_url(str(resp.url)),
                    "title": _page_title(html),
                    "text": _visible_text(html, 300),
                    "trace": trace.hops[-6:],
                },
            )

        raise LoginFlowError(
            "Sign-in did not complete within the redirect budget.", detail={"trace": trace.hops[-8:]}
        )


def _page_title(html: str) -> str:
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    return re.sub(r"\s+", " ", match.group(1)).strip()[:120] if match else ""


async def password_login(
    username: str,
    password: str,
    settings: Settings,
    *,
    trace: LoginTrace | None = None,
) -> TokenBundle:
    """Try Moodle's plain ``/login/token.php`` endpoint."""
    trace = trace if trace is not None else LoginTrace()
    url = f"{settings.site_url}{settings.token_path}"
    trace.add(f"POST {url} (username/password → token)")

    payload = {
        "username": username,
        "password": password,
        "service": settings.service,
    }
    timeout = httpx.Timeout(settings.request_timeout, connect=15.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=BASE_HEADERS) as client:
            resp = await client.post(
                url,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.HTTPError as exc:
        raise UpstreamError(f"Network error contacting the token endpoint: {exc}") from exc

    try:
        data: dict[str, Any] = resp.json()
    except ValueError as exc:
        raise UpstreamError(
            "The token endpoint did not return JSON.", detail={"status": resp.status_code}
        ) from exc

    if "token" in data:
        trace.add("Token endpoint accepted the credentials")
        return TokenBundle(
            token=data["token"],
            private_token=data.get("privatetoken"),
            strategy="password",
        )

    errorcode = str(data.get("errorcode") or "")
    message = str(data.get("error") or "The token endpoint rejected the credentials.")
    if errorcode in {"invalidlogin", "invalidlogin", "usernotfound"}:
        raise InvalidCredentials(message)
    raise LoginFlowError(f"Token endpoint error: {message}", detail={"errorcode": errorcode})


async def acquire_tokens(
    username: str,
    password: str,
    settings: Settings,
    *,
    strategy: str = "auto",
    trace: LoginTrace | None = None,
) -> TokenBundle:
    """Obtain a Moodle token using the requested strategy.

    ``auto`` tries the cheap password endpoint first and falls back to the full
    SSO flow — correct for both password-enabled and SSO-only Moodle sites.
    """
    trace = trace if trace is not None else LoginTrace()
    username = username.strip()

    if strategy == "password":
        return await password_login(username, password, settings, trace=trace)

    if strategy == "mobile_sso":
        return await mobile_sso_login(username, password, settings, trace=trace)

    if strategy != "auto":
        raise ValueError(f"unknown strategy: {strategy}")

    try:
        return await password_login(username, password, settings, trace=trace)
    except InvalidCredentials:
        trace.add("Password endpoint declined; switching to SSO flow")
    except LoginFlowError as exc:
        trace.add(f"Password endpoint unavailable ({exc.code}); switching to SSO flow")

    return await mobile_sso_login(username, password, settings, trace=trace)


# ---------------------------------------------------------------------------
# Session-cookie path (the MFA-friendly route)
# ---------------------------------------------------------------------------
def parse_cookie_header(raw: str) -> dict[str, str]:
    """Parse cookies from whatever the user pasted.

    Accepts ``"MoodleSession=abc; MDL_SSP_SessID=def"`` (as Chrome/Edge DevTools
    shows it), a JSON object, or newline-separated ``name=value`` pairs.
    """
    text = (raw or "").strip().strip('"').strip("'")
    if not text:
        return {}

    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict):
            return {str(k).strip(): str(v) for k, v in data.items() if str(k).strip()}

    cookies: dict[str, str] = {}
    for chunk in re.split(r"[;\n\r]+", text):
        chunk = chunk.strip().rstrip(",")
        if not chunk:
            continue

        if "=" in chunk:
            name, _, value = chunk.partition("=")
        elif "\t" in chunk:
            # DevTools "Application → Cookies" copies tab-separated columns:
            # Name, Value, Domain, Path, Expires, …
            columns = [c.strip() for c in chunk.split("\t")]
            non_empty = [c for c in columns if c]
            if len(non_empty) < 2:
                continue
            if columns[0].lower() in {"name", "cookie", "key"}:
                continue  # pasted header row
            name, value = columns[0], columns[1]
        else:
            continue

        name, value = name.strip(), value.strip()
        if not name or name.lower() in {"name", "value", "cookie", "key"}:
            continue
        cookies[name] = value
    return cookies


async def token_from_cookies(
    cookies: Mapping[str, str],
    settings: Settings,
    *,
    trace: LoginTrace | None = None,
) -> TokenBundle:
    """Mint a web-service token from an already-authenticated browser session.

    This is the MFA escape hatch. Once a human has completed Passport York **and
    Duo** in a real browser, no second factor is needed to call ``launch.php``:
    Moodle sees a valid session and immediately redirects to
    ``moodlemobile://token=…``. We just need the session cookies.
    """
    trace = trace if trace is not None else LoginTrace()
    if not cookies:
        raise SessionNotAuthenticated("No cookies supplied.")

    host = httpx.URL(settings.site_url).host
    jar = httpx.Cookies()
    for name, value in cookies.items():
        jar.set(name, value, domain=host, path="/")

    if not any(name.lower().startswith("moodlesession") for name in cookies):
        trace.add("warning: no MoodleSession cookie was supplied")

    passport = secrets.token_hex(16)
    url = _launch_url(settings, passport)
    trace.add(f"GET {_sanitize_url(url)} (with {len(cookies)} session cookie(s))")

    timeout = httpx.Timeout(settings.request_timeout, connect=15.0)
    async with httpx.AsyncClient(
        cookies=jar, follow_redirects=False, timeout=timeout, headers=BASE_HEADERS
    ) as client:
        try:
            resp = await client.get(url)
        except httpx.HTTPError as exc:
            raise UpstreamError(f"Network error while minting the token: {exc}") from exc

        for _ in range(10):
            location = resp.headers.get("location")
            if not (resp.is_redirect and location):
                break

            target = urljoin(str(resp.url), location)
            if target.startswith(f"{settings.urlscheme}://"):
                trace.add("Moodle accepted the session and returned a token")
                return parse_token_redirect(target, settings.urlscheme)

            trace.add(f"{resp.status_code} → {_sanitize_url(target)}")
            lowered = target.lower()
            # Any bounce back into SSO means the cookies are not authenticated.
            if any(marker in lowered for marker in ("shib.yorku.ca", "/idp/", "passportyork", "saml")):
                raise SessionNotAuthenticated(
                    "Those cookies are not an authenticated eClass session — Moodle "
                    "redirected back to single sign-on. Sign in fully (including Duo) "
                    "in the browser, then copy the MoodleSession cookie.",
                    detail={"redirected_to": _sanitize_url(target)},
                )

            try:
                resp = await client.get(target)
            except httpx.HTTPError as exc:
                raise UpstreamError(f"Network error following {_sanitize_url(target)}: {exc}") from exc

    raise SessionNotAuthenticated(
        "Moodle did not issue a token from this session.",
        detail={
            "status": resp.status_code,
            "url": _sanitize_url(str(resp.url)),
            "title": _page_title(resp.text if resp.text else ""),
        },
    )
