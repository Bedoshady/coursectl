# API and MCP reference

Everything this server exposes, in one place. Generated from the live OpenAPI schema
and the running MCP server, so it matches the code.

- **HTTP API** — 22 paths, 24 operations, bearer-token auth
- **MCP server** — 13 tools, stdio or streamable HTTP

---

## HTTP API

Base URL `http://127.0.0.1:8787`. Interactive docs at `/docs`, machine schema at
`/openapi.json`.

### Authentication

Every route except `/`, `/health`, `/docs` and `POST /v1/auth/login` requires one of:

```
Authorization: Bearer ek_...
X-API-Key: ek_...
```

`{course}` accepts a numeric course id, a shortname (`EECS1012`), or a
case-insensitive substring of the full course name.

### meta

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Self-describing endpoint list |
| `GET` | `/health` | Liveness + version |

### auth

| Method | Path | Body / params | Purpose |
| --- | --- | --- | --- |
| `POST` | `/v1/auth/login` | `username`, `password`, or `cookie`, plus `strategy` (auto\|password\|mobile_sso), `registration_secret` | Exchange credentials or a browser session for an `ek_…` API key |
| `POST` | `/v1/auth/logout` | — | Revoke the API key and wipe the stored token |

### account

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/v1/me` | Moodle identity, site name, release, available WS functions |

### courses

| Method | Path | Params | Purpose |
| --- | --- | --- | --- |
| `GET` | `/v1/courses` | `refresh?` | All enrolled courses |
| `GET` | `/v1/courses/{course}` | — | One course's metadata |
| `GET` | `/v1/courses/{course}/contents` | — | Full section → module → file outline |
| `GET` | `/v1/courses/{course}/files` | — | Flat list of downloadable files |
| `GET` | `/v1/courses/{course}/assignments` | — | Assignments with due dates |
| `GET` | `/v1/courses/{course}/grades` | — | Grade items + running total |
| `GET` | `/v1/courses/{course}/announcements` | — | News-forum announcements |

### files

| Method | Path | Params / body | Purpose |
| --- | --- | --- | --- |
| `GET` | `/v1/files` | `q?` (filename/course filter) | Search files across every course |
| `POST` | `/v1/files/download` | `url`, `filename?`, `course_name?`, `overwrite?` | Fetch a file onto the server's disk |

`url` must be an eClass `pluginfile.php` URL; other hosts are refused (SSRF guard).

### calendar

| Method | Path | Params | Purpose |
| --- | --- | --- | --- |
| `GET` | `/v1/calendar` | `days?` (30), `limit?` (200) | Upcoming events |
| `GET` | `/v1/deadlines` | `days?` (14) | Only due-date events |
| `GET` | `/v1/calendar.ics` | `days?` (90) | iCalendar download for Google/Apple Calendar |

### coursework (aggregate)

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/v1/assignments` | Assignments across all courses |
| `GET` | `/v1/announcements` | Announcements across all courses |

### admin

Require `ECLASS_ADMIN_TOKEN` in the `Authorization` header. Disabled when unset.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/v1/admin/stats` | Account counts |
| `GET` | `/v1/admin/accounts` | List accounts (no tokens or hashes) |
| `DELETE` | `/v1/admin/accounts/{username}` | Revoke an account |

### Error envelope

```json
{ "error": "mfa_required", "message": "…", "detail": { "…": "…" } }
```

| `error` | HTTP | Meaning |
| --- | --- | --- |
| `unauthorized` | 401 | Missing / unknown / revoked API key |
| `invalid_credentials` | 401 | Passport York rejected the password |
| `mfa_required` | 401 | Password accepted, second factor demanded — use `--browser` / `--cookie` |
| `session_not_authenticated` | 401 | Supplied cookies are not a logged-in session |
| `session_expired` | 401 | Stored Moodle token no longer accepted — sign in again |
| `forbidden` | 403 | Not allowed (foreign download host, admin disabled) |
| `registration_closed` | 403 | New accounts need `ECLASS_REGISTRATION_SECRET` |
| `not_found` | 404 | No matching course / file |
| `rate_limited` | 429 | Reserved |
| `login_flow_error` | 502 | Unrecognised page in the SSO flow |
| `upstream_error` | 502 | eClass returned an error or unparseable payload |

---

## MCP server

```powershell
eclass mcp --account you   # stdio (harnesses)
eclass mcp --account you --http --port 8790
```

The server acts as exactly one account: `--account`, `ECLASS_MCP_ACCOUNT`, or
`ECLASS_MCP_API_KEY`.

| Tool | Parameters | Returns |
| --- | --- | --- |
| `whoami` | — | Account identity + site info |
| `list_courses` | — | `id`, `shortname`, `fullname`, `enddate` |
| `get_course_contents` | `course` | Sections → modules → files |
| `list_course_files` | `course` | Files with `fileurl`, size, mime, section |
| `search_files` | `query` | Files matching filename/module/course |
| `download_file` | `fileurl?`, `course?`, `filename?`, `overwrite?` | Local path + size |
| `get_upcoming_deadlines` | `days?` (14) | Events/deadlines |
| `get_calendar` | `days?` (30) | Full event list |
| `export_calendar` | `days?` (120) | Writes `eclass-calendar.ics`, returns path |
| `get_assignments` | `course?` | Assignments with due dates |
| `get_grades` | `course` | Grade items + total |
| `get_announcements` | `course?` | News forums |
| `get_announcement_posts` | `forum_id`, `limit?` | Newest posts in a forum |

Every tool returns JSON text. Errors come back as
`{"error": "…", "message": "…"}` so an agent can recover instead of crashing.

Example prompts these support:

- "What's due in the next week?"
- "Download the latest lecture PDF for EECS 1012."
- "Export my calendar and tell me where the file is."
- "What did my professor announce in MATH 1013?"
- "What's my grade breakdown in EECS 1012?"

---

## Upstream Moodle functions used

All through `POST /webservice/rest/server.php` with `moodlewsrestformat=json`:

| Function | Used by |
| --- | --- |
| `core_webservice_get_site_info` | `whoami`, `/v1/me`, login verification |
| `core_enrol_get_users_courses` | `list_courses` |
| `core_course_get_contents` | contents, files |
| `core_calendar_get_action_events_by_timesort` | calendar, deadlines (falls back to `core_calendar_get_calendar_events`) |
| `mod_assign_get_assignments` | assignments |
| `gradereport_user_get_grade_items` | grades |
| `mod_forum_get_forums_by_courses` | announcements |
| `mod_forum_get_forum_discussions` | announcement posts |

Files are fetched from `pluginfile.php` with `?token=<wstoken>` appended, and are
restricted to the configured eClass host.
