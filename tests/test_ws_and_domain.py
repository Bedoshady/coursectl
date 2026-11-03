"""Tests for the Moodle Web Services client and the domain layer."""

from __future__ import annotations

import httpx
import pytest
import respx

from coursectl.client import CourseClient, build_ics, _extract_files, Course
from coursectl.errors import SessionExpired, UpstreamError
from coursectl.ws import MoodleWebService, encode_params, flatten_params

SITE = "https://moodle.test"

SITE_INFO = {
    "sitename": "eClass",
    "username": "abc123",
    "fullname": "Doe, Jane",
    "userid": 42,
    "release": "5.0.1 (Build: 20251006)",
    "functions": [{"name": "core_enrol_get_users_courses"}],
}

COURSES = [
    {"id": 7, "shortname": "EECS1012", "fullname": "Computational Thinking", "category": 3,
     "visible": 1, "startdate": 1730000000, "enddate": 1738000000},
    {"id": 9, "shortname": "MATH1013", "fullname": "Applied Calculus", "category": 3,
     "visible": 1, "progress": 55.5},
]

CONTENTS = [
    {
        "id": 100, "name": "General", "section": 0, "visible": 1, "summary": "<p>Welcome</p>",
        "modules": [
            {
                "id": 500, "name": "Syllabus", "modname": "resource", "visible": 1,
                "description": "<p>Course outline</p>",
                "url": f"{SITE}/mod/resource/view.php?id=500",
                "contents": [
                    {"type": "file", "filename": "syllabus.pdf", "filepath": "/",
                     "filesize": 1024, "mimetype": "application/pdf",
                     "fileurl": f"{SITE}/pluginfile.php/1/mod_resource/content/0/syllabus.pdf",
                     "timemodified": 1730000000}
                ],
            }
        ],
    },
    {
        "id": 101, "name": "Week 5 — Trees", "section": 5, "visible": 1, "summary": "",
        "modules": [
            {
                "id": 501, "name": "Lecture 5", "modname": "resource", "visible": 1,
                "contents": [
                    {"type": "file", "filename": "lecture05.pdf", "filesize": 2048,
                     "mimetype": "application/pdf",
                     "fileurl": f"{SITE}/pluginfile.php/2/mod_resource/content/0/lecture05.pdf"},
                    {"type": "url", "filename": "reading", "fileurl": "https://example.com/r"},
                ],
            }
        ],
    },
]


# ---------------------------------------------------------------------------
# parameter encoding
# ---------------------------------------------------------------------------
def test_flatten_params_nested():
    out: dict[str, str] = {}
    flatten_params("options", {"userevents": True, "limit": [1, 2], "range": {"from": 5}}, out)
    assert out == {
        "options[userevents]": "1",
        "options[limit][0]": "1",
        "options[limit][1]": "2",
        "options[range][from]": "5",
    }


def test_encode_params_booleans_and_none():
    assert encode_params({"a": False, "b": None, "c": "x"}) == {"a": "0", "b": "", "c": "x"}


# ---------------------------------------------------------------------------
# WS call handling
# ---------------------------------------------------------------------------
@respx.mock
async def test_call_posts_expected_fields():
    route = respx.post(f"{SITE}/webservice/rest/server.php").mock(
        return_value=httpx.Response(200, json=SITE_INFO)
    )
    ws = MoodleWebService(SITE, "tok123")
    result = await ws.call("core_webservice_get_site_info")

    assert result["userid"] == 42
    sent = route.calls[0].request.content.decode()
    assert "wstoken=tok123" in sent
    assert "wsfunction=core_webservice_get_site_info" in sent
    assert "moodlewsrestformat=json" in sent


@respx.mock
async def test_calls_identify_the_client():
    """Transparency: an institution should be able to tell what is calling."""
    route = respx.post(f"{SITE}/webservice/rest/server.php").mock(
        return_value=httpx.Response(200, json=SITE_INFO)
    )
    ws = MoodleWebService(SITE, "tok", user_agent="coursectl/9.9 (+https://example.test)")
    await ws.call("core_webservice_get_site_info")
    assert route.calls[0].request.headers["user-agent"] == "coursectl/9.9 (+https://example.test)"


@respx.mock
async def test_call_maps_invalidtoken_to_session_expired():
    respx.post(f"{SITE}/webservice/rest/server.php").mock(
        return_value=httpx.Response(
            200,
            json={
                "exception": "moodle_exception",
                "errorcode": "invalidtoken",
                "message": "Ficha (token) no válida",
            },
        )
    )
    ws = MoodleWebService(SITE, "stale")
    with pytest.raises(SessionExpired):
        await ws.call("core_enrol_get_users_courses", userid=1)


@respx.mock
async def test_call_surfaces_generic_moodle_error():
    respx.post(f"{SITE}/webservice/rest/server.php").mock(
        return_value=httpx.Response(
            200,
            json={"exception": "moodle_exception", "errorcode": "nopermissions", "message": "No"},
        )
    )
    ws = MoodleWebService(SITE, "tok")
    with pytest.raises(UpstreamError) as excinfo:
        await ws.call("gradereport_user_get_grade_items", courseid=1)
    assert excinfo.value.detail["errorcode"] == "nopermissions"


@respx.mock
async def test_call_tolerates_php_warning_prefix():
    respx.post(f"{SITE}/webservice/rest/server.php").mock(
        return_value=httpx.Response(
            200,
            text='<br /><b>Warning</b>: something noisy<br />{"sitename":"eClass"}',
        )
    )
    ws = MoodleWebService(SITE, "tok")
    assert (await ws.call("core_webservice_get_site_info"))["sitename"] == "eClass"


def test_authorized_url_appends_token_once():
    ws = MoodleWebService(SITE, "tok123")
    url = f"{SITE}/pluginfile.php/1/mod_resource/content/0/a.pdf"
    assert ws.authorized_url(url) == f"{url}?token=tok123"
    # Existing query params are preserved, and the token is not duplicated.
    with_query = f"{SITE}/pluginfile.php/1/x/a.pdf?forcedownload=1"
    out = ws.authorized_url(with_query)
    assert "forcedownload=1" in out and out.count("token=") == 1


@respx.mock
async def test_download_streams_to_disk(tmp_path):
    url = f"{SITE}/pluginfile.php/1/mod_resource/content/0/syllabus.pdf"
    respx.get(url__startswith=url).mock(
        return_value=httpx.Response(
            200, content=b"%PDF-1.4 fake", headers={"content-type": "application/pdf"}
        )
    )
    ws = MoodleWebService(SITE, "tok")
    dest = tmp_path / "nested" / "syllabus.pdf"
    written = await ws.download(url, dest)

    assert written.read_bytes() == b"%PDF-1.4 fake"
    # Second call is a no-op unless overwrite is requested.
    written.write_bytes(b"cached")
    await ws.download(url, dest)
    assert written.read_bytes() == b"cached"


@respx.mock
async def test_download_refuses_html_login_page(tmp_path):
    url = f"{SITE}/pluginfile.php/1/x/a.pdf"
    respx.get(url__startswith=url).mock(
        return_value=httpx.Response(
            200, text="<html>login</html>", headers={"content-type": "text/html"}
        )
    )
    ws = MoodleWebService(SITE, "tok")
    with pytest.raises(UpstreamError):
        await ws.download(url, tmp_path / "a.pdf")
    assert not (tmp_path / "a.pdf").exists()


# ---------------------------------------------------------------------------
# domain layer
# ---------------------------------------------------------------------------
def test_extract_files_ignores_non_file_contents():
    course = Course(id=7, shortname="EECS1012", fullname="Computational Thinking")
    files = _extract_files(CONTENTS, course)
    assert [f.filename for f in files] == ["syllabus.pdf", "lecture05.pdf"]
    assert files[1].section == "Week 5 — Trees"
    assert files[0].course_name == "Computational Thinking"


@respx.mock
async def test_client_course_resolution_by_shortname_and_fuzzy():
    respx.post(f"{SITE}/webservice/rest/server.php").mock(
        side_effect=lambda request: httpx.Response(200, json=_dispatch(request))
    )
    client = CourseClient(MoodleWebService(SITE, "tok"), SITE)

    assert (await client.find_course("EECS1012")).id == 7
    assert (await client.find_course("9")).id == 9
    assert (await client.find_course("calculus")).id == 9

    from coursectl.errors import NotFound

    with pytest.raises(NotFound):
        await client.find_course("physics")


def _dispatch(request: httpx.Request):
    body = request.content.decode()
    if "wsfunction=core_webservice_get_site_info" in body:
        return SITE_INFO
    if "wsfunction=core_enrol_get_users_courses" in body:
        return COURSES
    if "wsfunction=core_course_get_contents" in body:
        return CONTENTS
    return {}


@respx.mock
async def test_client_course_contents_shape():
    respx.post(f"{SITE}/webservice/rest/server.php").mock(
        side_effect=lambda request: httpx.Response(200, json=_dispatch(request))
    )
    client = CourseClient(MoodleWebService(SITE, "tok"), SITE)
    payload = await client.course_contents("EECS1012")

    assert payload["course"]["shortname"] == "EECS1012"
    assert payload["sections"][1]["name"] == "Week 5 — Trees"
    assert payload["sections"][1]["modules"][0]["files"][0]["filename"] == "lecture05.pdf"


def test_is_allowed_file_url_blocks_other_hosts():
    client = CourseClient(MoodleWebService(SITE, "tok"), SITE)
    assert client.is_allowed_file_url(f"{SITE}/pluginfile.php/1/a.pdf")
    assert not client.is_allowed_file_url("https://evil.example/a.pdf")
    assert not client.is_allowed_file_url("file:///etc/passwd")


def test_build_ics_escapes_and_emits_events():
    events = [
        {
            "id": 1,
            "name": "Assignment 1 due",
            "course_name": "EECS 1012",
            "start": "2026-11-10T23:59:00+00:00",
            "description": "Submit; bring, notes",
            "url": f"{SITE}/mod/assign/view.php?id=1",
        },
        {"id": 2, "name": "no date"},
    ]
    ics = build_ics(events)
    assert ics.startswith("BEGIN:VCALENDAR")
    assert "DTSTART:20261110T235900Z" in ics
    assert "SUMMARY:Assignment 1 due \\(EECS 1012\\)" in ics or "Assignment 1 due (EECS 1012)" in ics
    assert "\\;" in ics and "\\," in ics
    assert ics.count("BEGIN:VEVENT") == 1
    assert ics.endswith("END:VCALENDAR\r\n")
