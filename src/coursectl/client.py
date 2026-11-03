"""High-level eClass operations: courses, contents, files, deadlines, grades.

This module is the single source of truth for behaviour. The HTTP API and the
MCP server are thin wrappers over it, so both stay in sync.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

from .errors import Forbidden, NotFound, UpstreamError
from .ws import MoodleWebService

# File types we treat as "content" when listing course files.
FILE_CONTENT_TYPE = "file"


def _ts(value: int | None) -> str | None:
    if not value:
        return None
    return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat(timespec="seconds")


def _slug(value: str, limit: int = 60) -> str:
    cleaned = re.sub(r"[^\w.\- ]+", "", value or "").strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    return (cleaned or "item")[:limit]


@dataclass(slots=True)
class CourseFile:
    """One downloadable file attached to a course module."""

    filename: str
    fileurl: str
    filesize: int
    mimetype: str
    timemodified: str | None
    course_id: int
    course_name: str
    section: str
    module: str
    module_id: int | None = None
    filepath: str = "/"

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "filesize": self.filesize,
            "mimetype": self.mimetype,
            "timemodified": self.timemodified,
            "course_id": self.course_id,
            "course_name": self.course_name,
            "section": self.section,
            "module": self.module,
            "module_id": self.module_id,
            "fileurl": self.fileurl,
        }


@dataclass(slots=True)
class Course:
    id: int
    shortname: str
    fullname: str
    category: int | None = None
    visible: bool = True
    startdate: str | None = None
    enddate: str | None = None
    idnumber: str | None = None
    progress: float | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "shortname": self.shortname,
            "fullname": self.fullname,
            "category": self.category,
            "visible": self.visible,
            "startdate": self.startdate,
            "enddate": self.enddate,
            "idnumber": self.idnumber,
            "progress": self.progress,
        }


class CourseClient:
    """Convenience wrapper around one authenticated Moodle account."""

    def __init__(self, ws: MoodleWebService, site_url: str) -> None:
        self.ws = ws
        self.site_url = site_url.rstrip("/")
        self._host = urlsplit(self.site_url).hostname or ""
        self._site_info: dict[str, Any] | None = None
        self._courses: list[Course] | None = None

    # -- identity --------------------------------------------------------
    async def site_info(self, *, refresh: bool = False) -> dict[str, Any]:
        if self._site_info is None or refresh:
            self._site_info = await self.ws.call("core_webservice_get_site_info")
        return self._site_info

    async def user_id(self) -> int:
        info = await self.site_info()
        return int(info["userid"])

    # -- courses ---------------------------------------------------------
    async def courses(self, *, refresh: bool = False) -> list[Course]:
        if self._courses is not None and not refresh:
            return self._courses
        raw = await self.ws.call("core_enrol_get_users_courses", userid=await self.user_id())
        courses = [
            Course(
                id=int(c.get("id", 0)),
                shortname=str(c.get("shortname") or ""),
                fullname=str(c.get("fullname") or c.get("displayname") or ""),
                category=c.get("category"),
                visible=bool(c.get("visible", True)),
                startdate=_ts(c.get("startdate")),
                enddate=_ts(c.get("enddate")),
                idnumber=c.get("idnumber"),
                progress=_progress(c),
                raw=c,
            )
            for c in (raw or [])
        ]
        courses.sort(key=lambda c: c.fullname.lower())
        self._courses = courses
        return courses

    async def find_course(self, reference: str | int) -> Course:
        """Resolve a course by numeric id, shortname, or a fuzzy name match."""
        courses = await self.courses()
        text = str(reference).strip()
        if text.isdigit():
            num = int(text)
            for course in courses:
                if course.id == num:
                    return course
        lowered = text.lower()
        for course in courses:
            if course.shortname.lower() == lowered or course.idnumber == text:
                return course
        for course in courses:
            if lowered in course.fullname.lower() or lowered in course.shortname.lower():
                return course
        raise NotFound(f"No enrolled course matches {reference!r}.")

    async def course_contents(self, reference: str | int) -> dict[str, Any]:
        course = await self.find_course(reference)
        sections = await self.ws.call("core_course_get_contents", courseid=course.id)
        return {
            "course": course.to_dict(),
            "sections": [
                {
                    "id": s.get("id"),
                    "name": s.get("name") or ("General" if s.get("section") == 0 else ""),
                    "section": s.get("section"),
                    "visible": bool(s.get("visible", 1)),
                    "summary": _clean_html(s.get("summary")),
                    "modules": [_module_summary(m) for m in (s.get("modules") or [])],
                }
                for s in (sections or [])
            ],
        }

    # -- files -----------------------------------------------------------
    async def course_files(self, reference: str | int) -> list[CourseFile]:
        course = await self.find_course(reference)
        sections = await self.ws.call("core_course_get_contents", courseid=course.id)
        return _extract_files(sections or [], course)

    async def all_files(self) -> list[CourseFile]:
        files: list[CourseFile] = []
        for course in await self.courses():
            try:
                files.extend(await self.course_files(course.id))
            except (UpstreamError, NotFound):
                continue
        return files

    async def search_files(self, query: str) -> list[CourseFile]:
        needle = query.strip().lower()
        matches = [
            f
            for f in await self.all_files()
            if needle in f.filename.lower()
            or needle in f.module.lower()
            or needle in f.course_name.lower()
        ]
        matches.sort(key=lambda f: (f.course_name.lower(), f.section.lower(), f.filename.lower()))
        return matches

    def is_allowed_file_url(self, url: str) -> bool:
        """Only fetch files from the eClass host — never an arbitrary URL (SSRF guard)."""
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"}:
            return False
        return (parts.hostname or "").lower() == self._host.lower()

    async def download(
        self,
        url: str,
        download_root: Path,
        *,
        filename: str | None = None,
        course_name: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        if not self.is_allowed_file_url(url):
            raise Forbidden("Refusing to download from a host other than eClass.")

        name = filename or Path(urlsplit(url).path).name or "download.bin"
        name = _slug(name, limit=120)
        folder = download_root / _slug(course_name or "general", limit=40)
        destination = folder / name

        if destination.exists() and not overwrite:
            return {
                "path": str(destination),
                "filename": destination.name,
                "size": destination.stat().st_size,
                "cached": True,
            }

        await self.ws.download(url, destination, overwrite=overwrite)
        return {
            "path": str(destination),
            "filename": destination.name,
            "size": destination.stat().st_size,
            "cached": False,
        }

    # -- deadlines / calendar -------------------------------------------
    async def upcoming_events(self, days: int = 30, limit: int = 100) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        return await self.events_between(now, now + timedelta(days=days), limit=limit)

    async def events_between(
        self,
        start: datetime,
        end: datetime,
        *,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Calendar "action" events (assignment due dates, quiz windows, …)."""
        try:
            raw = await self.ws.call(
                "core_calendar_get_action_events_by_timesort",
                timesortfrom=int(start.timestamp()),
                timesortto=int(end.timestamp()),
                limitnum=limit,
            )
            events = raw.get("events", raw if isinstance(raw, list) else [])
            return [_event_summary(e) for e in events]
        except UpstreamError:
            pass

        # Fallback for services that only expose the general calendar function.
        try:
            raw = await self.ws.call(
                "core_calendar_get_calendar_events",
                events={
                    "courseids": [c.id for c in await self.courses()],
                    "eventids": [],
                    "groupids": [],
                },
                options={
                    "userevents": True,
                    "siteevents": True,
                    "timestart": int(start.timestamp()),
                    "timeend": int(end.timestamp()),
                },
            )
            return [_event_summary(e) for e in raw.get("events", [])]
        except UpstreamError as exc:
            raise UpstreamError(f"Could not read the calendar: {exc.message}") from exc

    # -- coursework ------------------------------------------------------
    async def assignments(self, references: Iterable[str | int] | None = None) -> list[dict[str, Any]]:
        if references is None:
            course_ids = [c.id for c in await self.courses()]
            names = {c.id: c.fullname for c in await self.courses()}
        else:
            chosen = [await self.find_course(r) for r in references]
            course_ids = [c.id for c in chosen]
            names = {c.id: c.fullname for c in chosen}

        if not course_ids:
            return []

        raw = await self.ws.call("mod_assign_get_assignments", courseids=course_ids)
        out: list[dict[str, Any]] = []
        for course in raw.get("courses", []) if isinstance(raw, dict) else []:
            cid = course.get("id")
            for a in course.get("assignments", []) or []:
                out.append(
                    {
                        "id": a.get("id"),
                        "cmid": a.get("cmid"),
                        "course_id": cid,
                        "course_name": names.get(cid, course.get("fullname", "")),
                        "name": a.get("name"),
                        "due": _ts(a.get("duedate")),
                        "cutoff": _ts(a.get("cutoffdate")),
                        "opens": _ts(a.get("allowsubmissionsfromdate")),
                        "grade_max": a.get("grade"),
                        "completed": bool(a.get("completionsubmit")),
                        "intro": _clean_html(a.get("intro")),
                    }
                )
        out.sort(key=lambda a: (a["due"] is None, a["due"] or ""))
        return out

    async def grade_items(self, reference: str | int) -> dict[str, Any]:
        course = await self.find_course(reference)
        raw = await self.ws.call(
            "gradereport_user_get_grade_items", courseid=course.id, userid=0
        )
        grades = raw.get("usergrades") or []
        items: list[dict[str, Any]] = []
        course_grade = None
        if grades:
            course_grade = grades[0].get("coursegrade")
            for item in grades[0].get("gradeitems") or []:
                if item.get("itemtype") == "course":
                    continue
                items.append(
                    {
                        "name": item.get("itemname") or item.get("itemmodule"),
                        "itemtype": item.get("itemtype"),
                        "itemmodule": item.get("itemmodule"),
                        "grade": item.get("gradeformatted"),
                        "percentage": item.get("percentageformatted"),
                        "grademax": item.get("grademax"),
                        "grademin": item.get("grademin"),
                        "gradedate": _ts(item.get("gradedategraded")),
                    }
                )
        return {
            "course": course.to_dict(),
            "course_total": _clean_grade(course_grade),
            "items": items,
        }

    async def announcements(self, references: Iterable[str | int] | None = None) -> list[dict[str, Any]]:
        if references is None:
            courses = await self.courses()
        else:
            courses = [await self.find_course(r) for r in references]
        if not courses:
            return []

        raw = await self.ws.call(
            "mod_forum_get_forums_by_courses", courseids=[c.id for c in courses]
        )
        names = {c.id: c.fullname for c in courses}
        out = []
        for forum in raw if isinstance(raw, list) else []:
            if forum.get("type") != "news":
                continue
            out.append(
                {
                    "course_id": forum.get("course"),
                    "course_name": names.get(forum.get("course"), ""),
                    "forum_id": forum.get("id"),
                    "name": forum.get("name"),
                    "intro": _clean_html(forum.get("intro")),
                }
            )
        return out

    async def forum_posts(self, forum_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
        discussions = await self.ws.call(
            "mod_forum_get_forum_discussions",
            forumid=forum_id,
            sortorder=2,
            page=-1,
            perpage=limit,
        )
        out = []
        for d in discussions.get("discussions", []) if isinstance(discussions, dict) else []:
            out.append(
                {
                    "id": d.get("id"),
                    "subject": d.get("name"),
                    "author": d.get("userfullname"),
                    "created": _ts(d.get("timemodified") or d.get("created")),
                    "message": _clean_html(d.get("message")),
                    "discussion_id": d.get("discussion"),
                }
            )
        return out


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _clean_html(value: Any, limit: int = 600) -> str:
    if not value:
        return ""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", str(value))
    text = re.sub(r"(?s)<br\s*/?>", "\n", text)
    text = re.sub(r"(?s)</p>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:limit]


def _progress(course: dict[str, Any]) -> float | None:
    for key in ("progress", "completionpercentage"):
        if key in course and course[key] is not None:
            try:
                return round(float(course[key]), 1)
            except (TypeError, ValueError):
                return None
    return None


def _clean_grade(grade: Any) -> str | None:
    if not isinstance(grade, dict):
        return None
    return grade.get("formatted") or grade.get("percentageformatted")


def _module_summary(module: dict[str, Any]) -> dict[str, Any]:
    contents = module.get("contents") or []
    return {
        "id": module.get("id"),
        "cmid": module.get("instance"),
        "name": module.get("name"),
        "modname": module.get("modname"),
        "visible": bool(module.get("visible", 1)),
        "url": module.get("url"),
        "description": _clean_html(module.get("description"), limit=300),
        "files": [
            {
                "filename": c.get("filename"),
                "filesize": c.get("filesize"),
                "mimetype": c.get("mimetype"),
                "fileurl": c.get("fileurl"),
                "timemodified": _ts(c.get("timemodified")),
            }
            for c in contents
            if c.get("type") == FILE_CONTENT_TYPE
        ],
    }


def _extract_files(sections: list[dict[str, Any]], course: Course) -> list[CourseFile]:
    files: list[CourseFile] = []
    for section in sections:
        section_name = section.get("name") or (
            "General" if section.get("section") == 0 else f"Section {section.get('section')}"
        )
        for module in section.get("modules") or []:
            for content in module.get("contents") or []:
                if content.get("type") != FILE_CONTENT_TYPE:
                    continue
                url = content.get("fileurl") or ""
                if not url:
                    continue
                files.append(
                    CourseFile(
                        filename=content.get("filename") or "unnamed",
                        fileurl=url,
                        filesize=int(content.get("filesize") or 0),
                        mimetype=str(content.get("mimetype") or ""),
                        timemodified=_ts(content.get("timemodified")),
                        course_id=course.id,
                        course_name=course.fullname,
                        section=str(section_name),
                        module=str(module.get("name") or ""),
                        module_id=module.get("id"),
                        filepath=str(content.get("filepath") or "/"),
                    )
                )
    files.sort(key=lambda f: (f.section.lower(), f.filename.lower()))
    return files


def _event_summary(event: dict[str, Any]) -> dict[str, Any]:
    start = event.get("timestart") or event.get("timesort")
    duration = event.get("timeduration") or 0
    return {
        "id": event.get("id"),
        "name": event.get("name"),
        "course_id": event.get("courseid"),
        "course_name": event.get("coursename") or event.get("course", {}).get("fullname"),
        "module": event.get("modulename"),
        "eventtype": event.get("eventtype"),
        "start": _ts(start),
        "end": _ts(int(start) + int(duration)) if start and duration else None,
        "due": _ts(event.get("overdue")) if event.get("overdue") else None,
        "url": event.get("url"),
        "description": _clean_html(event.get("description"), limit=300),
    }


def build_ics(events: list[dict[str, Any]], calendar_name: str = "eClass") -> str:
    """Render events as an RFC 5545 iCalendar document."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//coursectl//eClass bridge//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_escape(calendar_name)}",
    ]
    for event in events:
        start_iso = event.get("start") or event.get("due")
        if not start_iso:
            continue
        try:
            start_dt = datetime.fromisoformat(start_iso)
        except ValueError:
            continue
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        end_dt = start_dt + timedelta(hours=1)
        if event.get("end"):
            try:
                end_dt = datetime.fromisoformat(event["end"])
            except ValueError:
                pass
        summary = event.get("course_name") or event.get("name") or "eClass event"
        if event.get("course_name") and event.get("name"):
            summary = f"{event['name']} ({event['course_name']})"
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:coursectl-{event.get('id') or abs(hash(start_iso))}@coursectl",
                f"DTSTAMP:{stamp}",
                f"DTSTART:{start_dt.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
                f"DTEND:{end_dt.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
                f"SUMMARY:{_ics_escape(summary)}",
                f"DESCRIPTION:{_ics_escape(event.get('description') or '')}",
            ]
        )
        if event.get("url"):
            lines.append(f"URL:{_ics_escape(event['url'])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def _ics_escape(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )
