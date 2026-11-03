"""Typed errors that map cleanly onto both HTTP responses and MCP tool results."""

from __future__ import annotations

from typing import Any


class CoursectlError(Exception):
    """Base class for every error this package raises deliberately."""

    code = "coursectl_error"
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


class InvalidCredentials(CoursectlError):
    """Passport York rejected the username/password pair."""

    code = "invalid_credentials"
    http_status = 401


class LoginFlowError(CoursectlError):
    """The SSO flow reached a page we do not recognise.

    Typically means York changed the login page or is showing a terms-of-use
    interstitial.
    """

    code = "login_flow_error"
    http_status = 502


class MfaRequired(CoursectlError):
    """The password was accepted, but a second factor is being demanded.

    Duo (or similar) cannot be completed by a headless HTTP client. Use
    ``coursectl login --browser`` or ``coursectl login --cookie``.
    """

    code = "mfa_required"
    http_status = 401


class SessionNotAuthenticated(CoursectlError):
    """Supplied browser cookies do not represent a logged-in eClass session."""

    code = "session_not_authenticated"
    http_status = 401


class TokenUnavailable(CoursectlError):
    """SSO succeeded but Moodle never handed back a web-service token."""

    code = "token_unavailable"
    http_status = 502


class SessionExpired(CoursectlError):
    """The stored Moodle token is no longer accepted; re-login is required."""

    code = "session_expired"
    http_status = 401


class Unauthorized(CoursectlError):
    """The caller's API key is missing, unknown, or revoked."""

    code = "unauthorized"
    http_status = 401


class Forbidden(CoursectlError):
    """The caller is authenticated but not allowed to do this."""

    code = "forbidden"
    http_status = 403


class RegistrationClosed(CoursectlError):
    code = "registration_closed"
    http_status = 403


class NotFound(CoursectlError):
    code = "not_found"
    http_status = 404


class UpstreamError(CoursectlError):
    """eClass returned an error or an unparseable response."""

    code = "upstream_error"
    http_status = 502


class RateLimited(CoursectlError):
    code = "rate_limited"
    http_status = 429


class DownloadError(CoursectlError):
    code = "download_error"
    http_status = 502
