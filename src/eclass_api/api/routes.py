"""HTTP API: a stable, documented facade over eClass.

Auth model
----------
1. ``POST /v1/auth/login`` with a Passport York username + password.
2. The server drives SSO, receives a Moodle web-service token, and keeps it.
3. The caller receives an opaque ``ek_...`` API key and uses it as a bearer
   token from then on. The password is forgotten immediately.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..eclass import build_ics
from ..errors import EClassError, Forbidden, InvalidCredentials, Unauthorized
from ..service import AccountContext, AccountService

API_VERSION = "0.1.0"


# -- schemas ----------------------------------------------------------------
class LoginRequest(BaseModel):
    username: str | None = Field(
        None, description="Passport York user ID (the part before @yorku.ca)"
    )
    password: str | None = Field(
        None, description="Passport York password — used once, never stored"
    )
    cookie: str | None = Field(
        None,
        description=(
            "Alternative to a password: an authenticated eClass session cookie string "
            "captured after completing Duo in a browser, e.g. 'MoodleSession=…; "
            "MDL_SSP_SessID=…'. The server exchanges it for a long-lived Moodle token."
        ),
    )
    strategy: str = Field(
        "auto",
        description="auto | password | mobile_sso",
        pattern="^(auto|password|mobile_sso)$",
    )
    registration_secret: str | None = Field(
        None, description="Required only when the server has open registration disabled"
    )


class DownloadRequest(BaseModel):
    url: str = Field(..., description="An eClass fileurl from /v1/.../files")
    filename: str | None = None
    course_name: str | None = None
    overwrite: bool = False


# -- dependencies -----------------------------------------------------------
def get_service(request: Request) -> AccountService:
    return request.app.state.service


async def current_account(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> AccountContext:
    """Accept either ``Authorization: Bearer <key>`` or ``X-API-Key: <key>``."""
    key = x_api_key
    if not key and authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value:
            key = value.strip()
        else:
            key = authorization.strip()
    if not key:
        raise Unauthorized("Provide an API key via 'Authorization: Bearer <key>' or 'X-API-Key'.")
    service: AccountService = request.app.state.service
    return service.resolve(key)


# -- app --------------------------------------------------------------------
def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="eClass API",
        version=API_VERSION,
        summary="Server-side token bridge for York University eClass, for apps and MCP clients.",
        description=__doc__,
    )
    app.state.settings = settings
    app.state.service = AccountService(settings)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(EClassError)
    async def _handle_eclass_error(_: Request, exc: EClassError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=exc.to_dict())

    _register_routes(app)
    return app


def _register_routes(app: FastAPI) -> None:
    @app.get("/", tags=["meta"])
    async def index() -> dict[str, Any]:
        return {
            "name": "eClass API",
            "version": API_VERSION,
            "site": app.state.settings.site_url,
            "docs": "/docs",
            "endpoints": [
                "POST /v1/auth/login",
                "GET  /v1/me",
                "GET  /v1/courses",
                "GET  /v1/courses/{course}/contents",
                "GET  /v1/courses/{course}/files",
                "GET  /v1/files?q=",
                "POST /v1/files/download",
                "GET  /v1/calendar?days=30",
                "GET  /v1/calendar.ics?days=90",
                "GET  /v1/deadlines?days=14",
                "GET  /v1/assignments",
                "GET  /v1/courses/{course}/assignments",
                "GET  /v1/courses/{course}/grades",
                "GET  /v1/courses/{course}/announcements",
                "GET  /v1/announcements",
                "POST /v1/auth/logout",
            ],
        }

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, Any]:
        return {"status": "ok", "version": API_VERSION}

    # -- auth ------------------------------------------------------------
    @app.post("/v1/auth/login", tags=["auth"])
    async def login(
        payload: LoginRequest,
        service: AccountService = Depends(get_service),
    ) -> dict[str, Any]:
        from ..auth import parse_cookie_header

        if payload.cookie:
            cookies = parse_cookie_header(payload.cookie)
            if not cookies:
                raise InvalidCredentials("Could not parse any cookies from the 'cookie' field.")
            account, api_key, trace = await service.login_via_cookies(cookies)
        else:
            if not payload.username or not payload.password:
                raise InvalidCredentials(
                    "Provide either a username and password, or a 'cookie' string."
                )
            account, api_key, trace = await service.login(
                payload.username,
                payload.password,
                strategy=payload.strategy,
                registration_secret=payload.registration_secret,
            )
        return {
            "account": account.public(token=api_key),
            "api_key": api_key,
            "usage": "Send 'Authorization: Bearer <api_key>' on every other request.",
            "trace": trace.hops,
        }

    @app.post("/v1/auth/logout", tags=["auth"])
    async def logout(
        ctx: AccountContext = Depends(current_account),
        service: AccountService = Depends(get_service),
    ) -> dict[str, Any]:
        service.revoke(ctx.account.username)
        return {"revoked": True, "username": ctx.account.username}

    # -- account ---------------------------------------------------------
    @app.get("/v1/me", tags=["account"])
    async def me(ctx: AccountContext = Depends(current_account)) -> dict[str, Any]:
        info = await ctx.client.site_info()
        return {
            "account": ctx.account.public(),
            "userid": info.get("userid"),
            "username": info.get("username"),
            "fullname": info.get("fullname"),
            "sitename": info.get("sitename"),
            "release": info.get("release"),
            "functions": info.get("functions"),
        }

    # -- courses ---------------------------------------------------------
    @app.get("/v1/courses", tags=["courses"])
    async def list_courses(
        refresh: bool = Query(False),
        ctx: AccountContext = Depends(current_account),
    ) -> dict[str, Any]:
        courses = await ctx.client.courses(refresh=refresh)
        return {"count": len(courses), "courses": [c.to_dict() for c in courses]}

    @app.get("/v1/courses/{course}", tags=["courses"])
    async def course_detail(
        course: str,
        ctx: AccountContext = Depends(current_account),
    ) -> dict[str, Any]:
        resolved = await ctx.client.find_course(course)
        return {"course": resolved.to_dict()}

    @app.get("/v1/courses/{course}/contents", tags=["courses"])
    async def course_contents(
        course: str,
        ctx: AccountContext = Depends(current_account),
    ) -> dict[str, Any]:
        return await ctx.client.course_contents(course)

    @app.get("/v1/courses/{course}/files", tags=["files"])
    async def course_files(
        course: str,
        ctx: AccountContext = Depends(current_account),
    ) -> dict[str, Any]:
        files = await ctx.client.course_files(course)
        return {"count": len(files), "files": [f.to_dict() for f in files]}

    @app.get("/v1/courses/{course}/assignments", tags=["coursework"])
    async def course_assignments(
        course: str,
        ctx: AccountContext = Depends(current_account),
    ) -> dict[str, Any]:
        items = await ctx.client.assignments([course])
        return {"count": len(items), "assignments": items}

    @app.get("/v1/courses/{course}/grades", tags=["coursework"])
    async def course_grades(
        course: str,
        ctx: AccountContext = Depends(current_account),
    ) -> dict[str, Any]:
        return await ctx.client.grade_items(course)

    @app.get("/v1/courses/{course}/announcements", tags=["coursework"])
    async def course_announcements(
        course: str,
        ctx: AccountContext = Depends(current_account),
    ) -> dict[str, Any]:
        items = await ctx.client.announcements([course])
        return {"count": len(items), "announcements": items}

    # -- files -----------------------------------------------------------
    @app.get("/v1/files", tags=["files"])
    async def all_files(
        q: str | None = Query(None, description="Optional filename/course filter"),
        ctx: AccountContext = Depends(current_account),
    ) -> dict[str, Any]:
        files = await (ctx.client.search_files(q) if q else ctx.client.all_files())
        return {"count": len(files), "files": [f.to_dict() for f in files]}

    @app.post("/v1/files/download", tags=["files"])
    async def download_file(
        payload: DownloadRequest,
        request: Request,
        ctx: AccountContext = Depends(current_account),
    ) -> dict[str, Any]:
        settings: Settings = request.app.state.settings
        if not ctx.client.is_allowed_file_url(payload.url):
            raise Forbidden("Only eClass file URLs can be downloaded by this server.")
        result = await ctx.client.download(
            payload.url,
            settings.download_dir,
            filename=payload.filename,
            course_name=payload.course_name,
            overwrite=payload.overwrite,
        )
        return result

    # -- calendar --------------------------------------------------------
    @app.get("/v1/calendar", tags=["calendar"])
    async def calendar(
        days: int = Query(30, ge=1, le=400),
        limit: int = Query(200, ge=1, le=500),
        ctx: AccountContext = Depends(current_account),
    ) -> dict[str, Any]:
        events = await ctx.client.upcoming_events(days=days, limit=limit)
        return {"days": days, "count": len(events), "events": events}

    @app.get("/v1/deadlines", tags=["calendar"])
    async def deadlines(
        days: int = Query(14, ge=1, le=400),
        ctx: AccountContext = Depends(current_account),
    ) -> dict[str, Any]:
        events = await ctx.client.upcoming_events(days=days, limit=200)
        due = [e for e in events if e.get("module") or e.get("eventtype") == "due"]
        return {"days": days, "count": len(due), "deadlines": due}

    @app.get("/v1/calendar.ics", tags=["calendar"], response_class=PlainTextResponse)
    async def calendar_ics(
        days: int = Query(90, ge=1, le=400),
        ctx: AccountContext = Depends(current_account),
    ) -> Response:
        events = await ctx.client.upcoming_events(days=days, limit=400)
        body = build_ics(events, calendar_name=f"eClass — {ctx.account.username}")
        return Response(
            content=body,
            media_type="text/calendar; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="eclass.ics"'},
        )

    # -- coursework aggregate -------------------------------------------
    @app.get("/v1/assignments", tags=["coursework"])
    async def all_assignments(ctx: AccountContext = Depends(current_account)) -> dict[str, Any]:
        items = await ctx.client.assignments()
        return {"count": len(items), "assignments": items}

    @app.get("/v1/announcements", tags=["coursework"])
    async def all_announcements(ctx: AccountContext = Depends(current_account)) -> dict[str, Any]:
        items = await ctx.client.announcements()
        return {"count": len(items), "announcements": items}

    # -- admin -----------------------------------------------------------
    def _require_admin(authorization: str | None) -> None:
        expected = app.state.settings.admin_token
        if not expected:
            raise Forbidden("Admin routes are disabled (set ECLASS_ADMIN_TOKEN).")
        supplied = ""
        if authorization:
            scheme, _, value = authorization.partition(" ")
            supplied = value.strip() if scheme.lower() == "bearer" and value else authorization.strip()
        if supplied != expected:
            raise Forbidden("Invalid admin token.")

    @app.get("/v1/admin/stats", tags=["admin"])
    async def admin_stats(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _require_admin(authorization)
        return app.state.service.stats()

    @app.get("/v1/admin/accounts", tags=["admin"])
    async def admin_accounts(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _require_admin(authorization)
        accounts = app.state.service.list_accounts()
        return {"count": len(accounts), "accounts": [a.public() for a in accounts]}

    @app.delete("/v1/admin/accounts/{username}", tags=["admin"])
    async def admin_revoke(
        username: str, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        _require_admin(authorization)
        revoked = app.state.service.revoke(username)
        return {"revoked": revoked, "username": username}
