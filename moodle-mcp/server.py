"""MCP server for Moodle, built on Moodle's REST web service API.

Configuration (environment variables):
    MOODLE_URL           Base URL of the Moodle site, e.g. https://moodle.example.edu
    MOODLE_TOKEN         Web service token (see get_token.py)
    MOODLE_DOWNLOAD_DIR  Optional folder for downloaded files (default: ~/Downloads/Moodle)
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

# httpx logs request URLs, and download URLs contain the token.
logging.getLogger("httpx").setLevel(logging.WARNING)

MOODLE_URL = os.environ.get("MOODLE_URL", "").rstrip("/")
MOODLE_TOKEN = os.environ.get("MOODLE_TOKEN", "")
DOWNLOAD_DIR = Path(os.environ.get("MOODLE_DOWNLOAD_DIR", "~/Downloads/Moodle")).expanduser()

mcp = MCPServer(
    name="moodle",
    instructions=(
        "Access the user's Moodle courses: course list, course contents and files, "
        "what's new, assignments and feedback, quizzes, deadlines, calendar, grades, "
        "progress, forums, pages and books, notifications and messages. Read-only. "
        "Call list_courses first to get course ids. Times are shown in the server's "
        "local time zone."
    ),
)

_userid: int | None = None


class MoodleError(ToolError):
    """Error whose message is shown to the client as the tool result."""


def _flatten(params: dict[str, Any], prefix: str = "") -> list[tuple[str, str]]:
    """Encode nested dicts/lists the way Moodle expects: courseids[0]=1&options[0][name]=x."""
    out: list[tuple[str, str]] = []
    for key, value in params.items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, dict):
            out.extend(_flatten(value, name))
        elif isinstance(value, (list, tuple)):
            out.extend(_flatten(dict(enumerate(value)), name))
        elif isinstance(value, bool):
            out.append((name, "1" if value else "0"))
        elif value is not None:
            out.append((name, str(value)))
    return out


async def call(function: str, **params: Any) -> Any:
    if not MOODLE_URL or not MOODLE_TOKEN:
        raise MoodleError("MOODLE_URL and MOODLE_TOKEN must be set (see README).")
    data = [
        ("wstoken", MOODLE_TOKEN),
        ("wsfunction", function),
        ("moodlewsrestformat", "json"),
        *_flatten(params),
    ]
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.post(
            f"{MOODLE_URL}/webservice/rest/server.php",
            content=urlencode(data),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    resp.raise_for_status()
    result = resp.json()
    if isinstance(result, dict) and "exception" in result:
        raise MoodleError(f"{function}: {result.get('message')} ({result.get('errorcode')})")
    return result


async def userid() -> int:
    global _userid
    if _userid is None:
        _userid = (await call("core_webservice_get_site_info"))["userid"]
    return _userid


def _ts(value: int | None) -> str:
    if not value:
        return "-"
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")


def _text(raw: str | None, limit: int = 2000) -> str:
    """Strip HTML from Moodle rich text and shorten it."""
    if not raw:
        return ""
    text = re.sub(r"<(br|/p|/div|/li|/h\d)[^>]*>", "\n", raw, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return text if len(text) <= limit else text[:limit] + " …"


def _moodle_error(body: bytes) -> str | None:
    """Return the message if a JSON response is a Moodle error, else None."""
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if isinstance(data, dict) and ("exception" in data or "errorcode" in data):
        return f"{data.get('message') or data.get('error')} ({data.get('errorcode')})"
    return None


def _with_token(fileurl: str) -> str:
    """Make a pluginfile URL downloadable with the web service token."""
    url = fileurl
    if "/webservice/pluginfile.php" not in url:
        url = url.replace("/pluginfile.php", "/webservice/pluginfile.php", 1)
    parts = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "token"]
    query.append(("token", MOODLE_TOKEN))
    return urlunparse(parts._replace(query=urlencode(query)))


def _safe_name(name: str) -> str:
    """Make a file or folder name safe; never returns '.', '..' or ''."""
    name = re.sub(r"[^\w\- .()]", "_", name).strip(" .")
    return name or "_"


def _check_status(resp: httpx.Response) -> None:
    """Raise on HTTP errors without the URL: download URLs carry the token."""
    if resp.is_error:
        raise MoodleError(f"Download failed: HTTP {resp.status_code} {resp.reason_phrase}")


def _check_site(file_url: str) -> None:
    if not MOODLE_URL or urlparse(file_url).netloc != urlparse(MOODLE_URL).netloc:
        raise MoodleError("Only files from the configured Moodle site can be downloaded.")


async def _download(client: httpx.AsyncClient, file_url: str, target: Path) -> int:
    """Download a Moodle file to target and return its size in bytes."""
    _check_site(file_url)
    target.parent.mkdir(parents=True, exist_ok=True)
    async with client.stream("GET", _with_token(file_url)) as resp:
        _check_status(resp)
        if "json" in resp.headers.get("content-type", ""):
            # Moodle reports errors as JSON, but files such as .ipynb or .json are JSON too.
            body = await resp.aread()
            error = _moodle_error(body)
            if error:
                raise MoodleError(f"Download failed: {error}")
            target.write_bytes(body)
            return len(body)
        with target.open("wb") as fh:
            async for chunk in resp.aiter_bytes():
                fh.write(chunk)
    return target.stat().st_size


async def _fetch_text(file_url: str) -> str:
    """Fetch a Moodle file (e.g. a book chapter) as text."""
    _check_site(file_url)
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.get(_with_token(file_url))
    _check_status(resp)
    error = _moodle_error(resp.content) if "json" in resp.headers.get("content-type", "") else None
    if error:
        raise MoodleError(error)
    return resp.text


async def _my_courses(course_id: int | None = None, include_past: bool = False) -> list[dict[str, Any]]:
    courses = await call("core_enrol_get_users_courses", userid=await userid())
    if course_id is not None:
        return [c for c in courses if c["id"] == course_id] or [{"id": course_id, "fullname": f"Course {course_id}"}]
    now = datetime.now().timestamp()
    return [c for c in courses if include_past or not (c.get("enddate") and c["enddate"] < now)]


def _course_folder(fullname: str) -> str:
    """'1234-Corporate Finance-2526_T1' -> 'Corporate Finance'."""
    name = re.sub(r"^\d+\s*-\s*", "", fullname)
    name = re.sub(r"\s*-\s*\d{4}_?\w*$", "", name)
    return _safe_name(name or fullname)


def _section_name(s: dict[str, Any]) -> str:
    return (s.get("name") or "").strip() or f"Section {s.get('section')}"


COMPLETION_STATES = {0: "not done", 1: "done", 2: "done (passed)", 3: "done (failed)"}

UPDATE_KINDS = {
    "configuration": "settings or description changed",
    "contentfiles": "new or changed files",
    "introfiles": "description files changed",
    "discussions": "new forum posts",
    "attempts": "your attempts",
    "questions": "questions changed",
    "grades": "grades",
    "gradeitems": "grades",
    "submissions": "submissions",
    "usersubmissions": "your submission",
    "entries": "new entries",
}

# Lines in quiz review HTML that only repeat the status or are navigation.
_QUIZ_NOISE = re.compile(r"^(//|Flag question|Remove flag|Question text|Question \d+|Correct|Incorrect|Partially correct|Mark [\d.,]+ out of [\d.,]+)$")


@mcp.tool()
async def site_info() -> str:
    """Show which Moodle site and user this server is connected to."""
    info = await call("core_webservice_get_site_info")
    return (
        f"Site: {info.get('sitename')} ({info.get('siteurl')})\n"
        f"User: {info.get('fullname')} (id {info.get('userid')}, {info.get('username')})\n"
        f"Moodle release: {info.get('release')}"
    )


@mcp.tool()
async def list_courses(include_past: bool = False) -> str:
    """List the courses the user is enrolled in, with their ids.

    Args:
        include_past: Also list courses that have already ended.
    """
    courses = await call("core_enrol_get_users_courses", userid=await userid())
    now = datetime.now().timestamp()
    lines = []
    for c in sorted(courses, key=lambda c: c.get("startdate") or 0, reverse=True):
        ended = c.get("enddate") and c["enddate"] < now
        if ended and not include_past:
            continue
        progress = c.get("progress")
        lines.append(
            f"- [{c['id']}] {c['fullname']} ({c.get('shortname', '')})"
            + (f", progress {round(progress)}%" if progress is not None else "")
            + (" [ended]" if ended else "")
        )
    return "\n".join(lines) or "No courses found."


@mcp.tool()
async def get_course_contents(course_id: int, section: str | None = None) -> str:
    """Show a course's sections, activities and files.

    Args:
        course_id: Course id from list_courses.
        section: Optional text to only show sections whose name contains it.
    """
    sections = await call("core_course_get_contents", courseid=course_id)
    out = []
    for s in sections:
        if section and section.lower() not in (s.get("name") or "").lower():
            continue
        if not s.get("uservisible", True):
            continue
        out.append(f"## {_section_name(s)}")
        summary = _text(s.get("summary"), 500)
        if summary:
            out.append(summary)
        for m in s.get("modules", []):
            if not m.get("uservisible", True):
                continue
            out.append(f"- [{m['modname']} {m['id']}] {m['name']}")
            desc = _text(m.get("description"), 300)
            if desc:
                out.append(f"    {desc}")
            for f in m.get("contents") or []:
                if f.get("type") == "file":
                    out.append(f"    file: {f['filename']} ({f.get('filesize', 0) // 1024} KB) url: {f['fileurl']}")
                elif f.get("type") == "url":
                    out.append(f"    link: {f.get('fileurl')}")
    return "\n".join(out) or "No contents found."


@mcp.tool()
async def list_assignments(course_id: int | None = None, only_open: bool = True) -> str:
    """List assignments with due dates and submission status.

    Args:
        course_id: Optional course id; all courses if omitted.
        only_open: Hide assignments whose due date has passed.
    """
    params: dict[str, Any] = {}
    if course_id is not None:
        params["courseids"] = [course_id]
    data = await call("mod_assign_get_assignments", **params)
    now = datetime.now().timestamp()
    rows = []
    for course in data.get("courses", []):
        for a in course.get("assignments", []):
            due = a.get("duedate") or 0
            if only_open and due and due < now:
                continue
            rows.append((due or float("inf"), course["fullname"], a))
    rows.sort(key=lambda r: r[0])

    out = []
    for due, course_name, a in rows:
        status = ""
        try:
            sub = await call("mod_assign_get_submission_status", assignid=a["id"])
            last = (sub.get("lastattempt") or {}).get("submission") or {}
            status = f", status: {last.get('status', 'unknown')}"
        except MoodleError:
            pass
        out.append(
            f"- [{a['id']}] {a['name']} ({course_name}), due {_ts(a.get('duedate'))}{status}"
        )
        intro = _text(a.get("intro"), 400)
        if intro:
            out.append(f"    {intro}")
    return "\n".join(out) or "No assignments found."


@mcp.tool()
async def upcoming_deadlines(days: int = 14) -> str:
    """List upcoming deadlines and to-dos from the Moodle timeline.

    Args:
        days: How many days ahead to look.
    """
    now = int(datetime.now().timestamp())
    data = await call(
        "core_calendar_get_action_events_by_timesort",
        timesortfrom=now,
        timesortto=now + days * 86400,
        limitnum=50,
    )
    out = []
    for e in data.get("events", []):
        course = (e.get("course") or {}).get("fullname", "")
        out.append(f"- {_ts(e.get('timesort'))}  {e.get('name')}  ({course})  {e.get('url', '')}")
    return "\n".join(out) or f"No deadlines in the next {days} days."


@mcp.tool()
async def calendar_events(days: int = 14) -> str:
    """List calendar events (lectures, deadlines, site events) in the coming days.

    Args:
        days: How many days ahead to look.
    """
    now = int(datetime.now().timestamp())
    data = await call(
        "core_calendar_get_calendar_events",
        options={"userevents": 1, "siteevents": 1, "timestart": now, "timeend": now + days * 86400},
    )
    events = sorted(data.get("events", []), key=lambda e: e.get("timestart", 0))
    out = []
    for e in events:
        out.append(f"- {_ts(e.get('timestart'))}  {e.get('name')}  [{e.get('eventtype')}]")
    return "\n".join(out) or f"No events in the next {days} days."


@mcp.tool()
async def get_grades(course_id: int) -> str:
    """Show the user's grade items and grades in a course.

    Args:
        course_id: Course id from list_courses.
    """
    data = await call("gradereport_user_get_grade_items", courseid=course_id, userid=await userid())
    out = []
    for report in data.get("usergrades", []):
        for item in report.get("gradeitems", []):
            name = item.get("itemname") or ("Course total" if item.get("itemtype") == "course" else item.get("itemtype"))
            grade = item.get("gradeformatted") or "-"
            rng = item.get("rangeformatted") or ""
            pct = item.get("percentageformatted") or ""
            out.append(f"- {name}: {grade}" + (f" (range {rng})" if rng else "") + (f", {pct}" if pct and pct != "-" else ""))
    return "\n".join(out) or "No grades found."


@mcp.tool()
async def list_forum_posts(course_id: int, limit: int = 10) -> str:
    """List the latest discussions from all forums in a course (e.g. announcements).

    Args:
        course_id: Course id from list_courses.
        limit: Maximum number of discussions per forum.
    """
    forums = await call("mod_forum_get_forums_by_courses", courseids=[course_id])
    out = []
    for f in forums:
        out.append(f"## {f['name']} (forum {f['id']})")
        data = await call("mod_forum_get_forum_discussions", forumid=f["id"], sortorder=1, page=0, perpage=limit)
        for d in data.get("discussions", []):
            out.append(f"- {_ts(d.get('created'))}  {d.get('name')}  by {d.get('userfullname', '')} (discussion {d.get('discussion')})")
            msg = _text(d.get("message"), 400)
            if msg:
                out.append(f"    {msg}")
    return "\n".join(out) or "No forums found."


@mcp.tool()
async def read_discussion(discussion_id: int) -> str:
    """Read all posts of a forum discussion.

    Args:
        discussion_id: Discussion id from list_forum_posts.
    """
    data = await call("mod_forum_get_discussion_posts", discussionid=discussion_id, sortby="created", sortdirection="ASC")
    out = []
    for p in data.get("posts", []):
        author = (p.get("author") or {}).get("fullname", "")
        out.append(f"### {p.get('subject')} by {author}, {_ts(p.get('timecreated'))}\n{_text(p.get('message'), 4000)}")
    return "\n\n".join(out) or "No posts found."


@mcp.tool()
async def search_courses(query: str) -> str:
    """Search all courses on the site by name (not only enrolled ones).

    Args:
        query: Search text.
    """
    data = await call("core_course_search_courses", criterianame="search", criteriavalue=query, page=0, perpage=20)
    out = [f"- [{c['id']}] {c['fullname']} ({c.get('shortname', '')})" for c in data.get("courses", [])]
    return "\n".join(out) or "No courses found."


@mcp.tool()
async def download_file(file_url: str, subfolder: str | None = None) -> str:
    """Download a course file (a url from get_course_contents) to the local download folder.

    Args:
        file_url: The pluginfile URL shown by get_course_contents.
        subfolder: Optional subfolder name, e.g. the course short name.
    """
    _check_site(file_url)
    target_dir = DOWNLOAD_DIR / _safe_name(subfolder) if subfolder else DOWNLOAD_DIR
    name = Path(unquote(urlparse(file_url).path)).name or "download"
    target = target_dir / _safe_name(name)
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        size = await _download(client, file_url, target)
    return f"Saved to {target} ({size // 1024} KB)"


# Activity types whose "files" are rendered HTML or links, not course materials.
_NOT_MATERIALS = {"page", "book", "url", "label", "forum", "quiz", "lesson", "glossary", "wiki"}


@mcp.tool()
async def download_files(
    course_id: int,
    section: str | None = None,
    module_id: int | None = None,
    subfolder: str | None = None,
    overwrite: bool = False,
) -> str:
    """Download all files of a course section, or of one activity such as a folder, keeping their structure.

    Give section or module_id; with neither, the whole course is downloaded.
    Files that already exist with the same size are skipped.

    Args:
        course_id: Course id from list_courses.
        section: Text a section name contains, e.g. "Week 1".
        module_id: Id of one activity (e.g. a folder) from get_course_contents.
        subfolder: Folder inside the download folder; default is the course name.
        overwrite: Download files again even if they already exist.
    """
    sections = await call("core_course_get_contents", courseid=course_id)
    if subfolder is None:
        course = (await _my_courses(course_id))[0]
        subfolder = _course_folder(course["fullname"])
    base = (DOWNLOAD_DIR / _safe_name(subfolder)).resolve()

    jobs: list[tuple[str, Path, int]] = []
    for s in sections:
        if not s.get("uservisible", True):
            continue
        sname = _section_name(s)
        if section and module_id is None and section.lower() not in sname.lower():
            continue
        for m in s.get("modules", []):
            if module_id is not None and m["id"] != module_id:
                continue
            if not m.get("uservisible", True) or m["modname"] in _NOT_MATERIALS:
                continue
            files = [f for f in m.get("contents") or [] if f.get("type") == "file" and f.get("fileurl")]
            # A single-file resource goes straight into the section folder; folders keep their own.
            folder = base / _safe_name(sname)
            if m["modname"] != "resource" or len(files) > 1:
                folder = folder / _safe_name(m["name"])
            for f in files:
                parts = [_safe_name(p) for p in (f.get("filepath") or "/").split("/") if p]
                target = folder.joinpath(*parts, _safe_name(f["filename"])).resolve()
                if not target.is_relative_to(base):
                    continue
                jobs.append((f["fileurl"], target, f.get("filesize") or 0))

    if not jobs:
        return "No files found for that selection."
    if len(jobs) > 300:
        raise MoodleError(f"{len(jobs)} files found. Choose a section or module to download fewer than 300.")

    saved, skipped, failed = [], [], []
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        for url, target, size in jobs:
            rel = target.relative_to(base)
            if not overwrite and target.exists() and target.stat().st_size == size:
                skipped.append(str(rel))
                continue
            try:
                got = await _download(client, url, target)
                saved.append(f"{rel} ({got // 1024} KB)")
            except (MoodleError, httpx.HTTPError) as exc:
                failed.append(f"{rel}: {exc}")

    out = [f"Folder: {base}", f"Downloaded {len(saved)} file(s), skipped {len(skipped)} existing, {len(failed)} failed."]
    out += [f"- {x}" for x in saved]
    if skipped:
        out.append("Already there: " + ", ".join(skipped))
    out += [f"FAILED {x}" for x in failed]
    return "\n".join(out)


@mcp.tool()
async def whats_new(days: int = 7, course_id: int | None = None) -> str:
    """Show what changed in the user's courses recently: new or updated files, activities and forum posts.

    Args:
        days: How many days back to look.
        course_id: Optional course id; all current courses if omitted.
    """
    since = int(datetime.now().timestamp()) - days * 86400
    courses = await _my_courses(course_id)

    async def one_course(course: dict[str, Any]) -> list[str]:
        cid = course["id"]
        try:
            updates, sections = await asyncio.gather(
                call("core_course_get_updates_since", courseid=cid, since=since),
                call("core_course_get_contents", courseid=cid),
            )
        except MoodleError as exc:
            return [f"## {course['fullname']}", f"(could not check: {exc})"]
        modules = {
            m["id"]: (_section_name(s), m)
            for s in sections
            if s.get("uservisible", True)
            for m in s.get("modules", [])
            if m.get("uservisible", True)
        }
        lines = []
        for inst in updates.get("instances", []):
            if inst.get("contextlevel") != "module" or inst["id"] not in modules:
                continue
            sname, m = modules[inst["id"]]
            kinds = {u["name"] for u in inst.get("updates", [])}
            labels = sorted({UPDATE_KINDS.get(k, k) for k in kinds})
            lines.append(f"- [{m['modname']} {m['id']}] {m['name']} ({sname}): {', '.join(labels)}")
            if "contentfiles" in kinds:
                for f in m.get("contents") or []:
                    if f.get("type") == "file" and max(f.get("timecreated") or 0, f.get("timemodified") or 0) >= since:
                        path = (f.get("filepath") or "/").lstrip("/")
                        lines.append(f"    file: {path}{f['filename']} ({_ts(f.get('timemodified'))}) url: {f['fileurl']}")
            if "discussions" in kinds and m["modname"] == "forum":
                try:
                    data = await call("mod_forum_get_forum_discussions", forumid=m["instance"], sortorder=1, page=0, perpage=20)
                except MoodleError:
                    continue
                for d in data.get("discussions", []):
                    if max(d.get("created") or 0, d.get("timemodified") or 0) >= since:
                        lines.append(f"    post: {_ts(d.get('created'))}  {d.get('name')} (discussion {d.get('discussion')})")
        return [f"## {course['fullname']} [{cid}]", *lines] if lines else []

    results = await asyncio.gather(*(one_course(c) for c in courses))
    out = [line for lines in results for line in lines]
    header = f"Changes since {_ts(since)} ({days} days)."
    return header + ("\n" + "\n".join(out) if out else "\nNothing new.")


@mcp.tool()
async def course_progress(course_id: int) -> str:
    """Show which activities in a course the user has completed, as Moodle tracks it.

    Args:
        course_id: Course id from list_courses.
    """
    sections = await call("core_course_get_contents", courseid=course_id)
    out, done, total = [], 0, 0
    for s in sections:
        rows = []
        for m in s.get("modules", []):
            cd = m.get("completiondata") or {}
            if not m.get("uservisible", True) or not cd.get("hascompletion"):
                continue
            state = cd.get("state", 0)
            total += 1
            done += state in (1, 2)
            when = f", {_ts(cd['timecompleted'])}" if cd.get("timecompleted") else ""
            mode = "auto" if cd.get("isautomatic") else "manual"
            rows.append(f"- [{'x' if state in (1, 2) else ' '}] {m['name']} ({m['modname']} {m['id']}, {mode}): {COMPLETION_STATES.get(state, state)}{when}")
        if rows:
            out += [f"## {_section_name(s)}", *rows]
    if not total:
        return "This course does not track activity completion."
    return f"{done} of {total} tracked activities done.\n" + "\n".join(out)


@mcp.tool()
async def quiz_results(course_id: int) -> str:
    """List the quizzes in a course with the user's attempts and grades.

    Args:
        course_id: Course id from list_courses.
    """
    data = await call("mod_quiz_get_quizzes_by_courses", courseids=[course_id])
    out = []
    for q in data.get("quizzes", []):
        maxgrade = q.get("grade") or 0
        out.append(f"## {q['name']} (quiz {q['id']}, module {q.get('coursemodule')})")
        if q.get("timeclose"):
            out.append(f"    closes {_ts(q['timeclose'])}")
        try:
            best = await call("mod_quiz_get_user_best_grade", quizid=q["id"])
            attempts = (await call("mod_quiz_get_user_attempts", quizid=q["id"], status="all")).get("attempts", [])
        except MoodleError as exc:
            out.append(f"    (not available: {exc})")
            continue
        if best.get("hasgrade"):
            out.append(f"    best grade: {best.get('grade'):g} / {maxgrade:g}")
        if not attempts:
            out.append("    no attempts yet")
        for a in attempts:
            grade = ""
            if a.get("sumgrades") is not None and q.get("sumgrades"):
                grade = f", {a['sumgrades'] / q['sumgrades'] * maxgrade:g} / {maxgrade:g}"
            out.append(f"    attempt {a['attempt']} (id {a['id']}): {a['state']}, {_ts(a.get('timestart'))} – {_ts(a.get('timefinish'))}{grade}")
    return "\n".join(out) or "No quizzes in this course."


@mcp.tool()
async def quiz_attempt_review(attempt_id: int) -> str:
    """Show the questions of a finished quiz attempt with the user's marks, if the teacher allows reviews.

    Args:
        attempt_id: Attempt id from quiz_results.
    """
    data = await call("mod_quiz_get_attempt_review", attemptid=attempt_id)
    out = [f"Grade: {data.get('grade')}"]
    for q in data.get("questions", []):
        mark = f"{q.get('mark')} / {q.get('maxmark')}" if q.get("mark") is not None else "not graded"
        out.append(f"\n### Question {q.get('questionnumber') or q.get('number') or q.get('slot')} ({q.get('type')}): {q.get('status')}, {mark}")
        text = "\n".join(l for l in _text(q.get("html"), 4000).splitlines() if not _QUIZ_NOISE.match(l.strip()))
        text = re.sub(r"\n\s*\n+", "\n\n", text).strip()[:1500]
        if text:
            out.append(text)
    return "\n".join(out)


@mcp.tool()
async def assignment_feedback(course_id: int | None = None) -> str:
    """Show grades and teacher feedback (comments, files) for the user's graded assignments.

    Args:
        course_id: Optional course id; all courses if omitted.
    """
    params: dict[str, Any] = {"courseids": [course_id]} if course_id is not None else {}
    data = await call("mod_assign_get_assignments", **params)
    out = []
    for course in data.get("courses", []):
        for a in course.get("assignments", []):
            try:
                sub = await call("mod_assign_get_submission_status", assignid=a["id"])
            except MoodleError:
                continue
            fb = sub.get("feedback")
            if not fb:
                continue
            grade = html.unescape(fb.get("gradefordisplay") or "-")
            out.append(f"## {a['name']} ({course['fullname']}, assignment {a['id']})")
            out.append(f"    grade: {_text(grade, 200)}, graded {_ts(fb.get('gradeddate'))}")
            for plugin in fb.get("plugins", []):
                for field in plugin.get("editorfields") or []:
                    text = _text(field.get("text"), 3000)
                    if text:
                        out.append(f"    {plugin.get('name', 'Feedback')}: {text}")
                for area in plugin.get("fileareas") or []:
                    for f in area.get("files") or []:
                        out.append(f"    feedback file: {f.get('filename')} url: {f.get('fileurl')}")
    return "\n".join(out) or "No graded assignments with feedback found."


@mcp.tool()
async def read_page(course_id: int, module_id: int) -> str:
    """Read the text of a page, book, label or link activity from get_course_contents.

    Args:
        course_id: Course id from list_courses.
        module_id: Activity id (the number after the type in get_course_contents).
    """
    sections = await call("core_course_get_contents", courseid=course_id)
    m = next((m for s in sections for m in s.get("modules", []) if m["id"] == module_id), None)
    if m is None:
        raise MoodleError(f"No activity {module_id} in course {course_id}.")
    kind = m["modname"]
    if kind == "page":
        pages = (await call("mod_page_get_pages_by_courses", courseids=[course_id])).get("pages", [])
        page = next((p for p in pages if p.get("coursemodule") == module_id), None)
        if page is None:
            raise MoodleError("Page not found.")
        return f"# {page['name']}\n\n{_text(page.get('intro'), 2000)}\n\n{_text(page.get('content'), 20000)}".strip()
    if kind == "book":
        files = {f.get("filepath"): f for f in m.get("contents") or [] if f.get("type") == "file"}
        structure = next((f for f in m.get("contents") or [] if f.get("filename") == "structure"), None)
        chapters = json.loads(structure["content"]) if structure and structure.get("content") else []
        out = [f"# {m['name']}"]

        def walk(items: list[dict[str, Any]], depth: int) -> list[tuple[int, dict[str, Any]]]:
            res = []
            for ch in items:
                res.append((depth, ch))
                res += walk(ch.get("subitems") or [], depth + 1)
            return res

        for depth, ch in walk(chapters, 2):
            f = files.get("/" + ch["href"].rsplit("/", 1)[0] + "/")
            out.append(f"\n{'#' * min(depth, 6)} {ch.get('title')}")
            if f:
                out.append(_text(await _fetch_text(f["fileurl"]), 8000))
        return "\n".join(out)
    if kind == "label":
        return _text(m.get("description"), 10000) or "(empty label)"
    if kind == "url":
        links = [f.get("fileurl") for f in m.get("contents") or [] if f.get("type") == "url"]
        return f"# {m['name']}\n{_text(m.get('description'), 2000)}\nlink: {', '.join(links) or '-'}"
    raise MoodleError(f"read_page supports page, book, label and url activities, not '{kind}'.")


@mcp.tool()
async def notifications(limit: int = 20, only_unread: bool = False) -> str:
    """List the user's Moodle notifications (forum posts, grades, deadlines), newest first.

    Args:
        limit: Maximum number of notifications.
        only_unread: Only show unread notifications.
    """
    data = await call("message_popup_get_popup_notifications", useridto=await userid(), newestfirst=1, limit=limit, offset=0)
    out = []
    for n in data.get("notifications", []):
        if only_unread and n.get("read"):
            continue
        flag = "" if n.get("read") else "[unread] "
        link = n.get("contexturl") or ""
        out.append(f"- {_ts(n.get('timecreated'))}  {flag}{n.get('subject')}" + (f"  {link}" if link else ""))
    unread = data.get("unreadcount")
    head = f"Unread: {unread}\n" if unread is not None else ""
    return head + ("\n".join(out) or "No notifications.")


@mcp.tool()
async def list_messages(limit: int = 10) -> str:
    """List the user's Moodle message conversations with the latest message.

    Args:
        limit: Maximum number of conversations.
    """
    me = await userid()
    data = await call("core_message_get_conversations", userid=me, limitfrom=0, limitnum=limit)
    out = []
    for c in data.get("conversations", []):
        others = [m["fullname"] for m in c.get("members", []) if m.get("id") != me]
        name = c.get("name") or ", ".join(others) or "Personal notes"
        unread = f", {c['unreadcount']} unread" if c.get("unreadcount") else ""
        out.append(f"- [{c['id']}] {name}{unread}")
        last = (c.get("messages") or [None])[0]
        if last:
            out.append(f"    {_ts(last.get('timecreated'))}: {_text(last.get('text'), 300)}")
    return "\n".join(out) or "No conversations."


@mcp.tool()
async def read_conversation(conversation_id: int, limit: int = 50) -> str:
    """Read the messages of a conversation from list_messages, oldest first.

    Args:
        conversation_id: Conversation id from list_messages.
        limit: Maximum number of recent messages.
    """
    me = await userid()
    data = await call(
        "core_message_get_conversation_messages",
        currentuserid=me, convid=conversation_id, limitfrom=0, limitnum=limit, newest=1,
    )
    names = {m["id"]: m.get("fullname", "") for m in data.get("members", [])}
    out = []
    for msg in reversed(data.get("messages", [])):
        who = "me" if msg.get("useridfrom") == me else names.get(msg.get("useridfrom"), str(msg.get("useridfrom")))
        out.append(f"{_ts(msg.get('timecreated'))} {who}: {_text(msg.get('text'), 3000)}")
    return "\n".join(out) or "No messages."


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
