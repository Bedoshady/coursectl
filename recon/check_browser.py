"""Verify Playwright can launch Chromium using the pre-existing browser download.

No browser re-download: we point PLAYWRIGHT_BROWSERS_PATH at the machine's
existing installation and confirm a real page loads.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eclass_api.browser import _ensure_browser_path


async def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

    _ensure_browser_path()
    import os

    print("PLAYWRIGHT_BROWSERS_PATH =", os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "<unset>"))

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            print("chromium version:", browser.version)
            page = await browser.new_page()
            await page.goto("https://eclass.yorku.ca/", wait_until="domcontentloaded", timeout=45_000)
            print("navigated to:", page.url[:100])
            title = await page.title()
            print("page title:", title)
            cookies = await page.context.cookies()
            names = sorted({c["name"] for c in cookies})
            print("cookies visible:", names)
        finally:
            await browser.close()

    print("\nPASS: browser automation is available and can reach eClass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
