"""Command-line interface.

    coursectl login <user>      sign in once, store the delegated token, get an API key
    coursectl serve             run the HTTP API
    coursectl mcp --account <u> run the MCP server over stdio
    coursectl accounts          list stored accounts
    coursectl revoke <user>     invalidate an account's API key and token
    coursectl check             live smoke test against eClass
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import os
import sys
from typing import Any

from .config import Settings, get_settings
from .errors import CoursectlError, MfaRequired
from .service import AccountService


def _setup_logging(level: str) -> None:
    # MCP speaks JSON-RPC on stdout, so all logging must go to stderr.
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # Keep per-request transport chatter out of the human-facing output.
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _ensure_utf8_stdio() -> None:
    """Windows consoles default to cp1252 and choke on the glyphs used below."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # detached or replaced stream
            pass


def _report_login(account, api_key: str, trace, settings: Settings, *, verbose: bool) -> None:
    print("\n✓ Signed in")
    print(f"  account      {account.username}")
    print(f"  display name {account.display_name}")
    print(f"  moodle user  {account.moodle_user_id}")
    print(f"  site         {account.site_url}")
    print(f"\n  API key      {api_key}")
    print("\n  Store this key now — only its hash is kept on the server.")
    print("  Try:  curl -H 'Authorization: Bearer <key>' "
          f"http://{settings.host}:{settings.port}/v1/courses")
    if trace and verbose:
        print("\n  SSO trace:")
        print(trace)


def _cmd_login(args: argparse.Namespace, settings: Settings) -> int:
    service = AccountService(settings)
    verbose = getattr(args, "verbose", False)

    try:
        if args.browser:
            print("Opening a browser — complete the Passport York login and the Duo prompt.")
            print("This window closes on its own once the session is captured.\n")
            account, api_key, trace = asyncio.run(
                service.login_via_browser(timeout=args.timeout)
            )
        elif args.cookie:
            from .auth import parse_cookie_header

            cookies = parse_cookie_header(args.cookie)
            if not cookies:
                print("✗ Could not parse any cookies from --cookie.", file=sys.stderr)
                return 2
            print(f"Using {len(cookies)} cookie(s): {', '.join(sorted(cookies))}\n")
            account, api_key, trace = asyncio.run(service.login_via_cookies(cookies))
        else:
            if not args.username:
                print("✗ A username is required unless you use --browser or --cookie.",
                      file=sys.stderr)
                return 2
            password = args.password or os.environ.get("COURSECTL_PASSWORD")
            if not password:
                password = getpass.getpass(f"Passport York password for {args.username}: ")
            try:
                account, api_key, trace = asyncio.run(
                    service.login(
                        args.username,
                        password,
                        strategy=args.strategy,
                        registration_secret=settings.registration_secret,
                    )
                )
            finally:
                del password
    except CoursectlError as exc:
        print(f"\n✗ Sign-in failed: {exc.message}", file=sys.stderr)
        if isinstance(exc, MfaRequired):
            print(
                "\n  York requires Duo two-factor authentication, which a headless client\n"
                "  cannot complete. Use one of these instead:\n"
                "    coursectl login --browser          (opens a window; handles Duo)\n"
                "    coursectl login --cookie \"...\"     (paste MoodleSession from DevTools)\n",
                file=sys.stderr,
            )
        elif exc.detail:
            print(f"  detail: {json.dumps(exc.detail)[:1200]}", file=sys.stderr)
        return 1

    _report_login(account, api_key, trace, settings, verbose=verbose)
    return 0


def _cmd_serve(args: argparse.Namespace, settings: Settings) -> int:
    import uvicorn

    from .api import create_app

    host = args.host or settings.host
    port = args.port or settings.port
    app = create_app(settings)
    print(f"eClass API on http://{host}:{port}  (docs at /docs)", file=sys.stderr)
    uvicorn.run(app, host=host, port=port, log_level=settings.log_level)
    return 0


def _cmd_mcp(args: argparse.Namespace, settings: Settings) -> int:
    from .mcp_server import run as run_mcp

    if args.account:
        settings.mcp_account = args.account
    if args.http:
        settings.mcp_transport = "http"
        settings.mcp_host = args.host or settings.mcp_host
        settings.mcp_port = args.port or settings.mcp_port
    run_mcp(settings)
    return 0


def _cmd_accounts(args: argparse.Namespace, settings: Settings) -> int:
    service = AccountService(settings)
    accounts = service.list_accounts()
    if not accounts:
        print("No accounts. Run: coursectl login <passport-york-id>")
        return 0
    if args.json:
        print(json.dumps([a.public() for a in accounts], indent=2))
        return 0
    print(f"{'USERNAME':<14}{'NAME':<28}{'KEY':<14}{'STATUS':<10}LAST LOGIN")
    for a in accounts:
        status = "active" if a.is_active else "revoked"
        print(
            f"{a.username:<14}{(a.display_name or '')[:27]:<28}"
            f"{a.api_key_prefix:<14}{status:<10}{a.last_login_at or ''}"
        )
    return 0


def _cmd_revoke(args: argparse.Namespace, settings: Settings) -> int:
    service = AccountService(settings)
    if service.revoke(args.username):
        print(f"✓ Revoked {args.username} (API key and stored token are dead).")
        return 0
    print(f"✗ No active account named {args.username}.")
    return 1


def _cmd_check(args: argparse.Namespace, settings: Settings) -> int:
    service = AccountService(settings)

    async def run() -> int:
        if args.account:
            ctx = service.client_for_username(args.account)
        else:
            active = [a for a in service.list_accounts() if a.is_active]
            if not active:
                print("No accounts stored. Run: coursectl login <id>")
                return 1
            ctx = service.build_context(active[0])

        print(f"→ site info for {ctx.account.username} …")
        info = await ctx.client.site_info()
        print(f"  {info.get('fullname')} @ {info.get('sitename')} (Moodle {info.get('release')})")
        print(f"  functions exposed: {len(info.get('functions') or [])}")

        courses = await ctx.client.courses()
        print(f"→ {len(courses)} enrolled courses")
        for course in courses[:8]:
            print(f"  • {course.shortname:<12} {course.fullname}")

        if courses:
            first = courses[0]
            files = await ctx.client.course_files(first.id)
            print(f"→ {len(files)} files in {first.shortname}")
            for f in files[:5]:
                print(f"  • {f.filename} ({f.filesize} bytes)")

        events = await ctx.client.upcoming_events(days=14, limit=10)
        print(f"→ {len(events)} events in the next 14 days")
        for e in events[:5]:
            print(f"  • {e.get('start')}  {e.get('name')}")
        return 0

    try:
        return asyncio.run(run())
    except CoursectlError as exc:
        print(f"✗ {exc.message}", file=sys.stderr)
        if exc.detail:
            print(f"  detail: {json.dumps(exc.detail)[:800]}", file=sys.stderr)
        return 1


def _add_verbose(parser: argparse.ArgumentParser, *, suppress_default: bool = False) -> None:
    """Declare ``--verbose`` so it works both before and after the subcommand.

    ``argparse`` binds a top-level flag before the subcommand only. For
    subparsers we suppress the default so the subcommand does not clobber a
    value the main parser already set.
    """
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS if suppress_default else False,
        help="show the SSO trace on login",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coursectl",
        description="Local API and MCP bridge for Moodle-based course platforms (e.g. York's eClass).",
    )
    _add_verbose(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser(
        "login",
        help="sign in and obtain an API key",
        description=(
            "Sign in three ways: with a password (fails if Duo is enforced), "
            "with --browser (complete Duo interactively), or with --cookie "
            "(reuse a browser session you already have)."
        ),
    )
    p_login.add_argument("username", nargs="?", help="Passport York user ID (not needed with --browser/--cookie)")
    p_login.add_argument("--password", help="password (omit to be prompted)")
    p_login.add_argument(
        "--strategy", default="auto", choices=["auto", "password", "mobile_sso"]
    )
    p_login.add_argument(
        "--browser",
        action="store_true",
        help="open a real browser and complete Duo interactively (recommended at York)",
    )
    p_login.add_argument(
        "--cookie",
        help="an authenticated session cookie header, e.g. 'MoodleSession=abc; MDL_SSP_SessID=def'",
    )
    p_login.add_argument(
        "--timeout", type=float, default=300.0, help="seconds to wait with --browser"
    )
    _add_verbose(p_login, suppress_default=True)
    p_login.set_defaults(func=_cmd_login)

    p_serve = sub.add_parser("serve", help="run the HTTP API")
    p_serve.add_argument("--host")
    p_serve.add_argument("--port", type=int)
    p_serve.set_defaults(func=_cmd_serve)

    p_mcp = sub.add_parser("mcp", help="run the MCP server")
    p_mcp.add_argument("--account", help="which stored account to act as")
    p_mcp.add_argument("--http", action="store_true", help="serve over streamable HTTP")
    p_mcp.add_argument("--host")
    p_mcp.add_argument("--port", type=int)
    p_mcp.set_defaults(func=_cmd_mcp)

    p_accounts = sub.add_parser("accounts", help="list stored accounts")
    p_accounts.add_argument("--json", action="store_true")
    p_accounts.set_defaults(func=_cmd_accounts)

    p_revoke = sub.add_parser("revoke", help="revoke an account")
    p_revoke.add_argument("username")
    p_revoke.set_defaults(func=_cmd_revoke)

    p_check = sub.add_parser("check", help="live smoke test")
    p_check.add_argument("--account")
    p_check.set_defaults(func=_cmd_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = get_settings()
    _ensure_utf8_stdio()
    _setup_logging(settings.log_level)
    try:
        return int(args.func(args, settings) or 0)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
