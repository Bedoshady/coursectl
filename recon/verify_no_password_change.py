"""Prove that the sign-in POST never carries account-mutating fields.

Runs the real York SSO chain with a bogus account, but intercepts the exact
form payload the server builds before it is sent. Passport York will reject the
credentials (as expected) — what matters is *which fields* were on the wire.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eclass_api import auth
from eclass_api.config import Settings
from eclass_api.errors import EClassError

captured: dict[str, str] = {}
real_builder = auth._build_credential_payload


def spy(form, username, password):
    payload = real_builder(form, username, password)
    captured.clear()
    captured.update(payload)
    return payload


async def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

    auth._build_credential_payload = spy  # type: ignore[assignment]
    settings = Settings()

    try:
        await auth.mobile_sso_login("zzzzzzzzz", "not-the-password", settings)
    except EClassError as exc:
        print(f"expected rejection: {exc.code}: {exc.message}\n")

    if not captured:
        print("FAIL: the credential POST was never built")
        return 1

    print("Fields actually submitted to Passport York:")
    for name, value in captured.items():
        shown = "<redacted>" if name.lower() == "password" else value[:40]
        flag = "  <-- ACCOUNT-MUTATING!" if name.lower() in auth.NEVER_SUBMIT else ""
        print(f"  {name:<16} = {shown}{flag}")

    offenders = [n for n in captured if n.lower() in auth.NEVER_SUBMIT]
    print()
    if offenders:
        print(f"FAIL: mutating fields were submitted: {offenders}")
        return 2

    print("PASS: no account-mutating field is present.")
    print(f"      'passchangewanted' sent? {'passchangewanted' in captured}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
