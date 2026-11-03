"""End-to-end tests of the HTTP API with a stubbed upstream.

The point is to verify the API contract — auth handling, status codes, payload
shapes — without touching eClass.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from coursectl.api import create_app
from coursectl.auth import LoginTrace
from coursectl.config import Settings
from coursectl.client import Course, CourseFile
from coursectl.errors import Forbidden, NotFound, Unauthorized
from coursectl.service import AccountContext
from coursectl.store import Account

API_KEY = "ck_testkey000000000000000000000000000"
ADMIN_TOKEN = "admin-secret"


class FakeClient:
    def __init__(self) -> None:
        self._courses = [
            Course(id=7, shortname="EECS1012", fullname="Computational Thinking"),
            Course(id=9, shortname="MATH1013", fullname="Applied Calculus"),
        ]

    async def site_info(self, *, refresh: bool = False):
        return {"userid": 42, "username": "abc123", "fullname": "Doe, Jane",
                "sitename": "eClass", "release": "5.0.1", "functions": []}

    async def courses(self, *, refresh: bool = False):
        return self._courses

    async def find_course(self, reference):
        text = str(reference).lower()
        for course in self._courses:
            if str(course.id) == text or text in course.fullname.lower() or text == course.shortname.lower():
                return course
        raise NotFound(f"No course {reference!r}")

    async def course_contents(self, reference):
        course = await self.find_course(reference)
        return {"course": course.to_dict(), "sections": []}

    async def course_files(self, reference):
        course = await self.find_course(reference)
        return [
            CourseFile(
                filename="lecture05.pdf",
                fileurl="https://moodle.test/pluginfile.php/2/mod_resource/content/0/lecture05.pdf",
                filesize=2048,
                mimetype="application/pdf",
                timemodified=None,
                course_id=course.id,
                course_name=course.fullname,
                section="Week 5",
                module="Lecture 5",
            )
        ]

    async def all_files(self):
        return await self.course_files("EECS1012")

    async def search_files(self, query):
        return [f for f in await self.all_files() if query.lower() in f.filename.lower()]

    def is_allowed_file_url(self, url: str) -> bool:
        return url.startswith("https://moodle.test/")

    async def download(self, url, root: Path, *, filename=None, course_name=None, overwrite=False):
        return {"path": str(Path(root) / "lecture05.pdf"), "filename": "lecture05.pdf",
                "size": 2048, "cached": False}

    async def upcoming_events(self, days: int = 30, limit: int = 100):
        return [{"id": 1, "name": "Assignment 1 due", "course_name": "EECS 1012",
                 "module": "assign", "start": "2026-11-10T23:59:00+00:00",
                 "eventtype": "due", "description": "Submit"}]

    async def assignments(self, references=None):
        return [{"id": 1, "name": "Assignment 1", "due": "2026-11-10T23:59:00+00:00",
                 "course_name": "EECS 1012"}]

    async def grade_items(self, reference):
        course = await self.find_course(reference)
        return {"course": course.to_dict(), "course_total": "82%", "items": []}

    async def announcements(self, references=None):
        return [{"course_name": "EECS 1012", "name": "News", "forum_id": 3}]


class FakeService:
    def __init__(self) -> None:
        self.account = Account(
            id="acct1", username="abc123", display_name="Doe, Jane", email="abc123@yorku.ca",
            moodle_user_id=42, site_url="https://moodle.test", token_enc="enc",
            private_token_enc=None, api_key_hash="hash", api_key_prefix="ck_testkey…",
            created_at="2026-01-01T00:00:00+00:00", last_login_at=None, last_used_at=None,
            revoked_at=None,
        )
        self.revoked: list[str] = []

    async def login(self, username, password, *, strategy="auto", registration_secret=None):
        assert password == "correct-horse"
        trace = LoginTrace()
        trace.add("fake sso hop")
        return self.account, API_KEY, trace

    def resolve(self, api_key: str) -> AccountContext:
        if api_key != API_KEY:
            raise Unauthorized("Unknown API key.")
        return AccountContext(account=self.account, client=FakeClient())

    def revoke(self, username: str) -> bool:
        self.revoked.append(username)
        return True

    def list_accounts(self):
        return [self.account]

    def stats(self):
        return {"accounts": 1, "active": 1, "revoked": 0, "site_url": "https://moodle.test"}


@pytest.fixture()
def app(tmp_path):
    settings = Settings(
        site_url="https://moodle.test",
        database_path=tmp_path / "t.db",
        secret_key_file=tmp_path / "k.key",
        download_dir=tmp_path / "dl",
        admin_token=ADMIN_TOKEN,
    )
    application = create_app(settings)
    application.state.service = FakeService()
    return application


@pytest.fixture()
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def auth(key: str = API_KEY) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_index_lists_endpoints(client):
    body = (await client.get("/")).json()
    assert "POST /v1/auth/login" in body["endpoints"]


async def test_openapi_schema_is_valid(client):
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json()["paths"]
    assert "/v1/auth/login" in paths
    assert "/v1/courses/{course}/files" in paths


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------
async def test_login_returns_key_and_trace(client):
    resp = await client.post(
        "/v1/auth/login", json={"username": "abc123", "password": "correct-horse"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["api_key"] == API_KEY
    assert body["account"]["username"] == "abc123"
    assert "correct-horse" not in resp.text
    assert body["trace"] == ["fake sso hop"]


async def test_login_rejects_unknown_strategy(client):
    resp = await client.post(
        "/v1/auth/login",
        json={"username": "abc123", "password": "correct-horse", "strategy": "telepathy"},
    )
    assert resp.status_code == 422


async def test_missing_key_is_401(client):
    resp = await client.get("/v1/courses")
    assert resp.status_code == 401
    assert resp.json()["error"] == "unauthorized"


async def test_wrong_key_is_401(client):
    resp = await client.get("/v1/courses", headers=auth("ck_wrong"))
    assert resp.status_code == 401


async def test_x_api_key_header_also_works(client):
    resp = await client.get("/v1/courses", headers={"X-API-Key": API_KEY})
    assert resp.status_code == 200


async def test_logout_revokes(client, app):
    resp = await client.post("/v1/auth/logout", headers=auth())
    assert resp.status_code == 200
    assert resp.json()["revoked"] is True
    assert app.state.service.revoked == ["abc123"]


# ---------------------------------------------------------------------------
# data routes
# ---------------------------------------------------------------------------
async def test_courses(client):
    body = (await client.get("/v1/courses", headers=auth())).json()
    assert body["count"] == 2
    assert body["courses"][0]["shortname"] == "EECS1012"


async def test_me(client):
    body = (await client.get("/v1/me", headers=auth())).json()
    assert body["userid"] == 42
    assert body["account"]["username"] == "abc123"


async def test_course_files(client):
    body = (await client.get("/v1/courses/EECS1012/files", headers=auth())).json()
    assert body["count"] == 1
    assert body["files"][0]["filename"] == "lecture05.pdf"


async def test_unknown_course_is_404(client):
    resp = await client.get("/v1/courses/physics/files", headers=auth())
    assert resp.status_code == 404
    assert resp.json()["error"] == "not_found"


async def test_calendar(client):
    body = (await client.get("/v1/calendar?days=7", headers=auth())).json()
    assert body["days"] == 7
    assert body["events"][0]["name"] == "Assignment 1 due"


async def test_calendar_ics_has_calendar_mime(client):
    resp = await client.get("/v1/calendar.ics", headers=auth())
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/calendar")
    assert "BEGIN:VCALENDAR" in resp.text
    assert "attachment" in resp.headers["content-disposition"]


async def test_deadlines(client):
    body = (await client.get("/v1/deadlines?days=3", headers=auth())).json()
    assert body["count"] == 1


async def test_grades(client):
    body = (await client.get("/v1/courses/EECS1012/grades", headers=auth())).json()
    assert body["course_total"] == "82%"


async def test_file_download_rejects_foreign_url(client):
    resp = await client.post(
        "/v1/files/download",
        headers=auth(),
        json={"url": "https://evil.example/malware.pdf"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "forbidden"


async def test_file_download_happy_path(client):
    resp = await client.post(
        "/v1/files/download",
        headers=auth(),
        json={
            "url": "https://moodle.test/pluginfile.php/2/mod_resource/content/0/lecture05.pdf",
            "filename": "lecture05.pdf",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["filename"] == "lecture05.pdf"


# ---------------------------------------------------------------------------
# admin
# ---------------------------------------------------------------------------
async def test_admin_requires_token(client):
    resp = await client.get("/v1/admin/stats")
    assert resp.status_code == 403


async def test_admin_rejects_bad_token(client):
    resp = await client.get("/v1/admin/stats", headers=auth("nope"))
    assert resp.status_code == 403


async def test_admin_stats_with_token(client):
    resp = await client.get("/v1/admin/stats", headers=auth(ADMIN_TOKEN))
    assert resp.status_code == 200
    assert resp.json()["accounts"] == 1


async def test_admin_disabled_without_configured_token(tmp_path):
    settings = Settings(
        site_url="https://moodle.test",
        database_path=tmp_path / "t.db",
        secret_key_file=tmp_path / "k.key",
        download_dir=tmp_path / "dl",
        admin_token=None,
    )
    application = create_app(settings)
    application.state.service = FakeService()
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/v1/admin/stats", headers=auth("anything"))
    assert resp.status_code == 403
    assert "disabled" in resp.json()["message"]
