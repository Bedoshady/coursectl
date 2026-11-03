"""Show exactly where Playwright looks for browser binaries on this machine."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eclass_api.browser import DEFAULT_BROWSER_PATHS, _ensure_browser_path


async def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

    print("1. PLAYWRIGHT_BROWSERS_PATH env var")
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    print(f"   {'SET   ' if env else 'unset '} {env or ''}")
    print("   -> when set, Playwright uses ONLY this directory")

    print("\n2. Built-in default for this OS")
    print(f"   {Path(os.environ.get('LOCALAPPDATA', '~')) / 'ms-playwright'}")

    print("\n3. Directories this project probes (browser.py DEFAULT_BROWSER_PATHS)")
    for candidate in DEFAULT_BROWSER_PATHS:
        exists = Path(candidate).is_dir()
        builds = sorted(p.name for p in Path(candidate).glob("chromium*")) if exists else []
        print(f"   {'[x]' if exists else '[ ]'} {candidate}")
        if builds:
            print(f"       builds: {', '.join(builds)}")

    _ensure_browser_path()
    print(f"\n4. After _ensure_browser_path(), env resolves to")
    print(f"   {os.environ.get('PLAYWRIGHT_BROWSERS_PATH', '<unset>')}")

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        exe = pw.chromium.executable_path
        print("\n5. Chromium binary Playwright actually launches")
        print(f"   {exe}")
        print(f"   exists: {Path(exe).exists()}")
        browser = await pw.chromium.launch(headless=True)
        print(f"   launched OK, version {browser.version}")
        await browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
