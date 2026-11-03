"""MCP server exposing eClass to an agent or harness.

Run over stdio (default, what desktop harnesses expect)::

    eclass mcp --account yournetid

or over streamable HTTP::

    eclass mcp --account yournetid --http --port 8790

The server acts as exactly one eClass account, chosen from ``--account`` /
``ECLASS_MCP_ACCOUNT`` (or the API key in ``ECLASS_MCP_API_KEY``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from .config import Settings, get_settings
from .eclass import EClassClient, build_ics
from .errors import EClassError, SessionExpired, Unauthorized
from .service import AccountContext, AccountService

log = logging.getLogger(__name__)


def _ok(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=None, default=str)


def _err(exc: Exception) -> str:
    if isinstance(exc, EClassError):
        return json.dumps(exc.to_dict(), ensure_ascii=False)
    return json.dumps({"error": "internal_error", "message": str(exc)}, ensure_ascii=False)


class _Context:
    """Holds one authenticated account context, rebuilt if the token dies."""

    def __init__(self, service: AccountService, settings: Settings) -> None:
        self.service = service
        self.settings = settings
        self._lock = asyncio.Lock()
        self._ctx: AccountContext | None = None

    def _resolve_sync(self) -> AccountContext:
        if self.settings.mcp_api_key:
            return self.service.resolve(self.settings.mcp_api_key)
        if self.settings.mcp_account:
            return self.service.client_for_username(self.settings.mcp_account)
        accounts = [a for a in self.service.list_accounts() if a.is_active]
        if len(accounts) == 1:
            return self.service.build_context(accounts[0])
        if not accounts:
            raise Unauthorized(
                "No eClass account is configured. Run `eclass login <user>` first, "
                "then start the MCP server with `eclass mcp --account <user>`."
            )
        names = ", ".join(a.username for a in accounts)
        raise Unauthorized(
            f"Several accounts exist ({names}); choose one with --account or ECLASS_MCP_ACCOUNT."
        )

    async def get(self, *, refresh: bool = False) -> AccountContext:
        async with self._lock:
            if self._ctx is None or refresh:
                self._ctx = await asyncio.to_thread(self._resolve_sync)
            return self._ctx

    async def invalidate(self) -> None:
        async with self._lock:
            self._ctx = None

    @property
    def client(self) -> EClassClient:
        raise RuntimeError("use `await ctx.get()`")

    async def call(self, fn, *args, **kwargs) -> Any:
        """Run ``fn(client, ...)`` with one automatic retry after a token death."""
        ctx = await self.get()
        try:
            return await fn(ctx.client, *args, **kwargs)
        except SessionExpired:
            await self.invalidate()
            ctx = await self.get(refresh=True)
            return await fn(ctx.client, *args, **kwargs)


def build_server(settings: Settings | None = None) -> MCPServer:
    settings = settings or get_settings()
    service = AccountService(settings)
    state = _Context(service, settings)

    server = MCPServer(
        name="eclass",
        title="York University eClass",
        version="0.1.0",
        instructions=(
            "Tools for a student's York University eClass (Moodle) account. "
            "Start with `whoami` or `list_courses` to discover course identifiers, then use "
            "`get_course_contents`, `list_course_files` or `download_file` to reach lecture "
            "material, `get_upcoming_deadlines` / `export_calendar` for dates, and "
            "`get_grades` for marks. Courses can be addressed by numeric id, shortname, or a "
            "case-insensitive substring of the course name."
        ),
    )

    def _download_root() -> Path:
        return Path(settings.download_dir)

    # -- identity --------------------------------------------------------
    @server.tool(description="Show which eClass account this server is acting as.")
    async def whoami() -> str:
        try:
            ctx = await state.get()
            info = await ctx.client.site_info()
            return _ok(
                {
                    "username": ctx.account.username,
                    "userid": info.get("userid"),
                    "fullname": info.get("fullname"),
                    "sitename": info.get("sitename"),
                    "release": info.get("release"),
                }
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the agent verbatim
            return _err(exc)

    # -- courses ---------------------------------------------------------
    @server.tool(description="List every course the student is enrolled in.")
    async def list_courses() -> str:
        try:
            courses = await state.call(lambda c: c.courses())
            return _ok(
                [
                    {
                        "id": c.id,
                        "shortname": c.shortname,
                        "fullname": c.fullname,
                        "enddate": c.enddate,
                    }
                    for c in courses
                ]
            )
        except Exception as exc:
            return _err(exc)

    @server.tool(description="Get the section/module outline of one course, including file names.")
    async def get_course_contents(course: str) -> str:
        try:
            return _ok(await state.call(lambda c: c.course_contents(course)))
        except Exception as exc:
            return _err(exc)

    @server.tool(description="List downloadable files (lecture PDFs, slides, …) in a course.")
    async def list_course_files(course: str) -> str:
        try:
            files = await state.call(lambda c: c.course_files(course))
            return _ok([f.to_dict() for f in files])
        except Exception as exc:
            return _err(exc)

    @server.tool(
        description=(
            "Search course files across all courses by filename, module or course name. "
            "Use this to find e.g. 'lecture 5' or 'syllabus'."
        )
    )
    async def search_files(query: str) -> str:
        try:
            files = await state.call(lambda c: c.search_files(query))
            return _ok([f.to_dict() for f in files])
        except Exception as exc:
            return _err(exc)

    @server.tool(
        description=(
            "Download a course file to the server's disk and return its local path. "
            "Accepts a `fileurl` from list_course_files, or a course plus filename."
        )
    )
    async def download_file(
        fileurl: str | None = None,
        course: str | None = None,
        filename: str | None = None,
        overwrite: bool = False,
    ) -> str:
        try:
            async def _run(client: EClassClient) -> dict[str, Any]:
                url = fileurl
                course_name = None
                if not url:
                    if not course or not filename:
                        raise EClassError(
                            "Provide either `fileurl`, or both `course` and `filename`."
                        )
                    resolved = await client.find_course(course)
                    course_name = resolved.fullname
                    files = await client.course_files(resolved.id)
                    needle = filename.lower()
                    match = next(
                        (f for f in files if f.filename.lower() == needle),
                        None,
                    ) or next((f for f in files if needle in f.filename.lower()), None)
                    if match is None:
                        raise EClassError(f"No file matching {filename!r} in {resolved.fullname}.")
                    url = match.fileurl
                    filename_actual = match.filename
                else:
                    filename_actual = filename

                if not client.is_allowed_file_url(url):
                    raise EClassError("That URL is not hosted by eClass.")

                return await client.download(
                    url,
                    _download_root(),
                    filename=filename_actual,
                    course_name=course_name,
                    overwrite=overwrite,
                )

            return _ok(await state.call(_run))
        except Exception as exc:
            return _err(exc)

    # -- calendar --------------------------------------------------------
    @server.tool(
        description="Upcoming deadlines and calendar events for the next N days (default 14)."
    )
    async def get_upcoming_deadlines(days: int = 14) -> str:
        try:
            events = await state.call(lambda c: c.upcoming_events(days=days, limit=250))
            return _ok(events)
        except Exception as exc:
            return _err(exc)

    @server.tool(description="Full calendar of eClass events for the next N days (default 30).")
    async def get_calendar(days: int = 30) -> str:
        try:
            events = await state.call(lambda c: c.upcoming_events(days=days, limit=400))
            return _ok(events)
        except Exception as exc:
            return _err(exc)

    @server.tool(
        description=(
            "Export the calendar as an .ics file on disk (importable into Google/Apple Calendar) "
            "and return the local path."
        )
    )
    async def export_calendar(days: int = 120) -> str:
        try:
            ctx = await state.get()
            events = await state.call(lambda c: c.upcoming_events(days=days, limit=500))
            body = build_ics(events, calendar_name=f"eClass — {ctx.account.username}")
            path = _download_root() / "eclass-calendar.ics"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
            return _ok({"path": str(path), "events": len(events), "days": days})
        except Exception as exc:
            return _err(exc)

    # -- coursework ------------------------------------------------------
    @server.tool(description="Assignments with due dates, optionally limited to one course.")
    async def get_assignments(course: str | None = None) -> str:
        try:
            items = await state.call(
                lambda c: c.assignments([course] if course else None)
            )
            return _ok(items)
        except Exception as exc:
            return _err(exc)

    @server.tool(description="Grade items and running total for one course.")
    async def get_grades(course: str) -> str:
        try:
            return _ok(await state.call(lambda c: c.grade_items(course)))
        except Exception as exc:
            return _err(exc)

    @server.tool(description="Announcements/news forums, optionally limited to one course.")
    async def get_announcements(course: str | None = None) -> str:
        try:
            items = await state.call(
                lambda c: c.announcements([course] if course else None)
            )
            return _ok(items)
        except Exception as exc:
            return _err(exc)

    @server.tool(description="List the newest posts in one announcement forum.")
    async def get_announcement_posts(forum_id: int, limit: int = 10) -> str:
        try:
            return _ok(await state.call(lambda c: c.forum_posts(forum_id, limit=limit)))
        except Exception as exc:
            return _err(exc)

    return server


def run(settings: Settings | None = None) -> None:
    """Blocking entry point used by the CLI."""
    settings = settings or get_settings()
    server = build_server(settings)
    transport = settings.mcp_transport.lower()
    if transport in {"http", "streamable-http", "streamable_http"}:
        server.run("streamable-http", host=settings.mcp_host, port=settings.mcp_port)
    else:
        server.run("stdio")
