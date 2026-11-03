"""Browser-assisted sign-in, for when a second factor is unavoidable.

York's coursectl login goes through Passport York and then **Duo**. No headless
HTTP client can satisfy that, and it should not try to. The honest solution is
to let a human complete the challenge once in a real browser, then capture the
resulting session and mint a long-lived Moodle token from it.

Two things make this pleasant rather than painful:

* The browser uses a **persistent profile** (``data/browser-profile``). Duo's
  "remember me for 30 days" therefore survives, so only the first login needs
  the phone.
* Completion is detected by *polling the token endpoint*, not by scraping the
  page. As soon as the session can mint a token, we are done — no guessing about
  which URL the SSO dance has reached.

Playwright is an optional dependency; this module is only imported when the
``--browser`` strategy is requested.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Mapping

from .auth import token_from_cookies
from .config import Settings
from .errors import CoursectlError, LoginFlowError, SessionNotAuthenticated

log = logging.getLogger(__name__)

DEFAULT_BROWSER_PATHS = (
    str(Path.home() / "AppData" / "Local" / "ms-playwright"),
    str(Path.home() / "Library" / "Caches" / "ms-playwright"),
    str(Path.home() / ".cache" / "ms-playwright"),
)


def _ensure_browser_path() -> None:
    """Point Playwright at an existing browser download if one is available."""
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    for candidate in DEFAULT_BROWSER_PATHS:
        if Path(candidate).is_dir() and any(Path(candidate).glob("chromium*")):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = candidate
            log.debug("using browsers from %s", candidate)
            return


def _require_playwright():
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise LoginFlowError(
            "Playwright is not installed. Run: pip install playwright "
            "(browsers are reused if you already have them)."
        ) from exc
    return async_playwright


async def _moodle_cookies(context) -> dict[str, str]:
    """Collect cookies for the eClass host from a live browser context."""
    cookies = await context.cookies()
    return {c["name"]: c["value"] for c in cookies if c.get("name") and c.get("value")}


async def login_with_browser(
    settings: Settings,
    *,
    profile_dir: Path | None = None,
    headless: bool = False,
    timeout: float = 300.0,
    poll_interval: float = 2.0,
) -> tuple[str, dict[str, str]]:
    """Open a browser, wait for the human to finish SSO + MFA, mint a token.

    Returns ``(token, cookies)``. Raises :class:`LoginFlowError` on timeout.
    """
    _ensure_browser_path()
    async_playwright = _require_playwright()

    profile = Path(profile_dir or (settings.download_dir.parent / "browser-profile"))
    profile.mkdir(parents=True, exist_ok=True)

    start_url = f"{settings.site_url}/my/"

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900},
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()

            log.info("Opening %s — sign in (including Duo) in the window.", start_url)
            try:
                await page.goto(start_url, wait_until="domcontentloaded", timeout=60_000)
            except Exception as exc:  # noqa: BLE001 - network hiccups are normal here
                log.warning("Initial navigation reported: %s", exc)

            waited = 0.0
            last_error: Exception | None = None
            while True:
                # The user closing the window is a distinct outcome from a slow
                # login, so report it immediately instead of waiting out the
                # whole timeout.
                if not context.pages:
                    raise LoginFlowError(
                        "The browser window was closed before sign-in completed.",
                        detail={"waited_seconds": round(waited)},
                    )
                if waited >= timeout:
                    raise LoginFlowError(
                        f"Timed out after {waited:.0f}s waiting for a signed-in session. "
                        "Finish the Passport York login and Duo prompt in the browser "
                        "window, then re-run.",
                        detail={"last_error": str(last_error) if last_error else None},
                    )

                await asyncio.sleep(poll_interval)
                waited += poll_interval

                try:
                    cookies = await _moodle_cookies(context)
                except Exception as exc:  # browser vanished mid-poll
                    raise LoginFlowError(
                        "The browser closed before sign-in completed.",
                        detail={"waited_seconds": round(waited), "error": str(exc)},
                    ) from exc

                if not cookies:
                    continue

                try:
                    # Completion test: can this session actually mint a token?
                    bundle = await token_from_cookies(cookies, settings)
                    log.info("Session is authenticated; token captured after %.0fs", waited)
                    return bundle.token, cookies
                except SessionNotAuthenticated as exc:
                    last_error = exc
                    continue
                except CoursectlError as exc:
                    last_error = exc
                    log.debug("still waiting: %s", exc.message)
                    continue
        finally:
            try:
                await context.close()
            except Exception:  # noqa: BLE001
                pass


async def cookies_present(settings: Settings, cookies: Mapping[str, str]) -> bool:
    """Quick check that a cookie set can mint a token, without storing anything."""
    try:
        await token_from_cookies(cookies, settings)
        return True
    except CoursectlError:
        return False
