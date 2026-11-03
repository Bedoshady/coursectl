"""Typed errors that map cleanly onto both HTTP responses and MCP tool results."""

from __future__ import annotations

from typing import Any


class EClassError(Exception):
    """Base class for every error this package raises deliberately."""

    code = "eclass_error"
    http_status = 500

    def __init__(self, message: str, *, detail: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"error": self.code, "message": self.message}
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload


class InvalidCredentials(EClassError):
    """Passport York rejected the username/password pair."""

    code = "invalid_credentials"
    http_status = 401


class LoginFlowError(EClassError):
    """The SSO flow reached a page we do not recognise.

    Typically means York changed the login page or is showing a terms-of-use
    interstitial.
    """

    code = "login_flow_error"
    http_status = 502


class MfaRequired(EClassError):
    """The password was accepted, but a second factor is being demanded.

    Duo (or similar) cannot be completed by a headless HTTP client. Use
    ``eclass login --browser`` or ``eclass login --cookie``.
    """

    code = "mfa_required"
    http_status = 401


class SessionNotAuthenticated(EClassError):
    """Supplied browser cookies do not represent a logged-in eClass session."""

    code = "session_not_authenticated"
    http_status = 401


class TokenUnavailable(EClassError):
    """SSO succeeded but Moodle never handed back a web-service token."""

    code = "token_unavailable"
    http_status = 502


class SessionExpired(EClassError):
    """The stored Moodle token is no longer accepted; re-login is required."""

    code = "session_expired"
    http_status = 401


class Unauthorized(EClassError):
    """The caller's API key is missing, unknown, or revoked."""

    code = "unauthorized"
    http_status = 401


class Forbidden(EClassError):
    """The caller is authenticated but not allowed to do this."""

    code = "forbidden"
    http_status = 403


class RegistrationClosed(EClassError):
    code = "registration_closed"
    http_status = 403


class NotFound(EClassError):
    code = "not_found"
    http_status = 404


class UpstreamError(EClassError):
    """eClass returned an error or an unparseable response."""

    code = "upstream_error"
    http_status = 502


class RateLimited(EClassError):
    code = "rate_limited"
    http_status = 429


class DownloadError(EClassError):
    code = "download_error"
    http_status = 502
