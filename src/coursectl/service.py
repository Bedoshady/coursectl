"""Account lifecycle: sign in, hold the delegated token, hand out API clients."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .auth import LoginTrace, TokenBundle, acquire_tokens, token_from_cookies
from .config import Settings
from .client import CourseClient
from .errors import RegistrationClosed, SessionExpired, Unauthorized
from .security import SecretBox, constant_time_equals, generate_api_key, hash_api_key
from .store import Account, Store
from .ws import MoodleWebService

log = logging.getLogger(__name__)


@dataclass
class AccountContext:
    """An authenticated account plus a ready-to-use eClass client."""

    account: Account
    client: CourseClient

    @property
    def username(self) -> str:
        return self.account.username


class AccountService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = Store(settings.database_path)
        self.secrets = SecretBox.load_or_create(Path(settings.secret_key_file))

    # -- sign in ---------------------------------------------------------
    async def login(
        self,
        username: str,
        password: str,
        *,
        strategy: str = "auto",
        registration_secret: str | None = None,
        allow_rotation: bool = True,
    ) -> tuple[Account, str, LoginTrace]:
        """Authenticate against Passport York and persist the delegated token.

        Returns the account, the caller's *new* API key, and a trace. The
        password itself is never stored anywhere.
        """
        username = username.strip()
        if not username or not password:
            raise Unauthorized("Both a username and a password are required.")

        if not self.settings.allow_open_registration:
            existing = self.store.get_account_by_username(username)
            if existing is None:
                expected = self.settings.registration_secret
                if not expected or not registration_secret or not constant_time_equals(
                    expected, registration_secret
                ):
                    raise RegistrationClosed(
                        "Registration is closed. Supply the configured registration secret."
                    )

        existing = self.store.get_account_by_username(username)
        if existing is not None and not allow_rotation:
            raise RegistrationClosed("This account already exists and re-issue is disabled.")

        trace = LoginTrace()
        tokens: TokenBundle = await acquire_tokens(
            username, password, self.settings, strategy=strategy, trace=trace
        )
        return await self._finalize(tokens, username_hint=username, trace=trace)

    async def login_via_cookies(
        self,
        cookies: Mapping[str, str],
        *,
        trace: LoginTrace | None = None,
    ) -> tuple[Account, str, LoginTrace]:
        """Provision an account from an authenticated browser session.

        This is the MFA path: the human completes Duo in a browser, we exchange
        the resulting session cookies for a long-lived Moodle token.
        """
        trace = trace if trace is not None else LoginTrace()
        tokens = await token_from_cookies(cookies, self.settings, trace=trace)
        return await self._finalize(tokens, username_hint=None, trace=trace)

    async def login_via_browser(
        self,
        *,
        headless: bool = False,
        timeout: float = 300.0,
        profile_dir: Path | None = None,
    ) -> tuple[Account, str, LoginTrace]:
        """Open a real browser so the user can satisfy Duo, then provision."""
        from .browser import login_with_browser

        trace = LoginTrace()
        token, cookies = await login_with_browser(
            self.settings, headless=headless, timeout=timeout, profile_dir=profile_dir
        )
        trace.add(f"browser session captured ({len(cookies)} cookies)")
        tokens = TokenBundle(token=token, strategy="browser")
        return await self._finalize(tokens, username_hint=None, trace=trace)

    async def _finalize(
        self,
        tokens: TokenBundle,
        *,
        username_hint: str | None,
        trace: LoginTrace,
    ) -> tuple[Account, str, LoginTrace]:
        """Verify the token, enforce registration policy, and persist the account."""
        ws = MoodleWebService(
            self.settings.site_url,
            tokens.token,
            timeout=self.settings.request_timeout,
            user_agent=self.settings.user_agent,
        )
        try:
            info = await ws.call("core_webservice_get_site_info")
        except SessionExpired as exc:
            raise SessionExpired(
                "Moodle issued a token but rejected it immediately.",
                detail={"trace": trace.hops},
            ) from exc

        # With a browser/cookie login we learn the identity from Moodle itself.
        username = (username_hint or info.get("username") or "").strip().lower()
        if not username:
            raise Unauthorized("Could not determine which eClass account this session belongs to.")

        if not self.settings.allow_open_registration:
            if self.store.get_account_by_username(username) is None:
                expected = self.settings.registration_secret
                if not expected:
                    raise RegistrationClosed(
                        "Registration is closed. Supply the configured registration secret."
                    )

        api_key, key_hash, key_prefix = generate_api_key()
        account = self.store.upsert_account(
            username=username,
            site_url=self.settings.site_url,
            token_enc=self.secrets.encrypt(tokens.token) or "",
            private_token_enc=self.secrets.encrypt(tokens.private_token),
            api_key_hash=key_hash,
            api_key_prefix=key_prefix,
            display_name=info.get("fullname"),
            email=info.get("useremail") or info.get("email"),
            moodle_user_id=info.get("userid"),
        )
        self.store.log_event(
            "login", account_id=account.id, detail=f"strategy={tokens.strategy}"
        )
        log.info("Account %s signed in via %s", account.username, tokens.strategy)
        return account, api_key, trace

    # -- lookups ---------------------------------------------------------
    def resolve(self, api_key: str) -> AccountContext:
        """Turn a bearer API key into an authenticated context."""
        if not api_key:
            raise Unauthorized("Missing API key.")

        account = self.store.get_account_by_api_key(hash_api_key(api_key))
        if account is None:
            raise Unauthorized("Unknown API key.")
        if not account.is_active:
            raise Unauthorized("This API key has been revoked.")

        self.store.touch_usage(account.id)
        return AccountContext(account=account, client=self.build_client(account))

    def client_for_username(self, username: str) -> AccountContext:
        """Used by the MCP server when it acts as a fixed, known account."""
        account = self.store.get_account_by_username(username.strip().lower())
        if account is None:
            raise Unauthorized(
                f"No stored account for {username!r}. Run `coursectl login` first."
            )
        if not account.is_active:
            raise Unauthorized("This account has been revoked.")
        return AccountContext(account=account, client=self.build_client(account))

    def build_context(self, account: Account) -> AccountContext:
        """Wrap an already-trusted account (no API key needed)."""
        if not account.is_active:
            raise Unauthorized("This account has been revoked.")
        return AccountContext(account=account, client=self.build_client(account))

    def build_client(self, account: Account) -> CourseClient:
        token = self.secrets.decrypt(account.token_enc)
        if not token:
            raise SessionExpired(
                f"No stored token for {account.username}; sign in again.", detail={"account": account.id}
            )
        ws = MoodleWebService(
            account.site_url or self.settings.site_url,
            token,
            timeout=self.settings.request_timeout,
            user_agent=self.settings.user_agent,
        )
        return CourseClient(ws, account.site_url or self.settings.site_url)

    # -- administration --------------------------------------------------
    def revoke(self, username: str) -> bool:
        account = self.store.get_account_by_username(username.strip().lower())
        if account is None:
            return False
        revoked = self.store.revoke_account(account.id)
        if revoked:
            self.store.log_event("revoke", account_id=account.id)
        return revoked

    def list_accounts(self) -> list[Account]:
        return self.store.list_accounts()

    def stats(self) -> dict[str, Any]:
        accounts = self.store.list_accounts()
        return {
            "accounts": len(accounts),
            "active": sum(1 for a in accounts if a.is_active),
            "revoked": sum(1 for a in accounts if not a.is_active),
            "site_url": self.settings.site_url,
        }
