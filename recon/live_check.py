"""Live check: drive the real York SSO chain with a deliberately invalid account.

Reaching ``InvalidCredentials`` (and not ``LoginFlowError``) proves that every hop
and every form on the live IdP is being parsed correctly — i.e. the only thing
standing between this server and a real token is a valid password.

Usage:  .\\.venv\\Scripts\\python.exe recon\\live_check.py
"""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, "src")

from eclass_api.auth import LoginTrace, mobile_sso_login, password_login
from eclass_api.config import Settings
from eclass_api.errors import EClassError, InvalidCredentials


async def main() -> int:
    settings = Settings()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

    print("=" * 70)
    print("1) Password endpoint (/login/token.php) with a bogus account")
    print("=" * 70)
    try:
        await password_login("zzzzzzzzz", "definitely-not-a-password", settings)
        print("   UNEXPECTED: got a token")
    except InvalidCredentials as exc:
        print(f"   ✓ invalid_credentials: {exc.message}")
    except EClassError as exc:
        print(f"   ~ {exc.code}: {exc.message}")

    print()
    print("=" * 70)
    print("2) Full mobile SSO chain with a bogus account")
    print("=" * 70)
    trace = LoginTrace()
    try:
        await mobile_sso_login("zzzzzzzzz", "definitely-not-a-password", settings, trace=trace)
        print("   UNEXPECTED: got a token")
        code = 1
    except InvalidCredentials as exc:
        print(f"   ✓ reached Passport York and got: {exc.message}")
        print("   → all hops parsed; a valid password is the only missing piece")
        code = 0
    except EClassError as exc:
        print(f"   ✗ {exc.code}: {exc.message}")
        if exc.detail:
            for key, value in exc.detail.items():
                print(f"     {key}: {value}")
        code = 2
    finally:
        print()
        print("   SSO trace:")
        print(trace)

    return code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
