# coursectl

> **Unofficial. Not affiliated with, endorsed by, or supported by York University or
> Moodle Pty Ltd.** "eClass" and "Passport York" are York University's names; "Moodle"
> is a trademark of Moodle Pty Ltd.
>
> **Runs locally. No telemetry.** Your password never leaves your machine. The server
> binds `127.0.0.1` by default and talks only to `eclass.yorku.ca`.
>
> **Use at your own risk.** Automating a login may violate your institution's acceptable
> use policy and your account could be suspended. You are responsible for your own
> account. Do not use this to access accounts other than your own, to redistribute course
> material, or to bypass any access control. See
> [docs/LEGAL-AND-RISK.md](docs/LEGAL-AND-RISK.md).

A server that logs in to **York University eClass** (Moodle) on your behalf, keeps the
resulting token **on the server**, and exposes your courses, files, deadlines and grades
as a clean HTTP API — plus an MCP server so an agent can use it directly.

Instead of a browser holding your Moodle session, this service holds a delegated
Moodle web-service token and hands *you* an opaque `ck_...` API key. Your password is
used once, in memory, and never stored.

```
your app / MCP client ──ck_…──►  coursectl  ──moodle token──►  eclass.yorku.ca
                                     │
                              Passport York SSO (server-side)
```

---

## Why this design

eClass forces single sign-on: every request to `eclass.yorku.ca` 303-redirects to
`shib.yorku.ca`, which delegates to `passportyork.yorku.ca`. There is no public
password API. So this project does what the official Moodle mobile app does — it
mints a real web-service token through the SSO flow — but does it **server-side,
with plain HTTP**.

The flow it drives (`src/coursectl/auth.py`):

```
GET  /admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=…&urlscheme=moodlemobile
  → 303  shib.yorku.ca/idp/profile/SAML2/Redirect/SSO?SAMLRequest=…
  → 302  …SSO?execution=e1s1                  ← LoginHandler interstitial
  → POST _eventId_proceed (csrf_token)        ← 302 …SSO?execution=e1s2
  → 302  /idp/Authn/RemoteUser?conversation=e1s2
  → 200  passportyork.yorku.ca/ppylogin       ← username `mli` + `password` + __alb* fields
  → POST credentials
  → 302  back through Shibboleth → eClass
  → 303  moodlemobile://token=<base64>
```

The base64 payload decodes to `signature:::token:::privatetoken`. From that point on
everything is ordinary, documented Moodle **Web Services** — no HTML scraping.

## Duo two-factor authentication

York enforces **Duo** after the Passport York password. No headless HTTP client can
satisfy an interactive second factor, and this server does not attempt to. Submitting
the password gets you as far as the Duo challenge, then the flow stops and reports
`mfa_required` — deliberately *not* `invalid_credentials`, so a correct password is
never misreported as wrong.

There are two ways through, both of which let a human satisfy Duo once and then keep a
long-lived token:

### Option A — `--browser` (easiest)

```powershell
coursectl login --browser
```

A Chromium window opens at eClass. Sign in with Passport York, approve the Duo push on
your phone, and the window closes by itself once the session is captured.

It uses a persistent profile in `data/browser-profile/`, so Duo's *"remember me for
30 days"* survives and later logins usually skip the phone entirely.

### Option B — `--cookie` (no browser automation)

1. Sign in to <https://eclass.yorku.ca> in your normal browser (including Duo).
2. DevTools → **Application → Cookies → https://eclass.yorku.ca**.
3. Copy the `MoodleSession` row (and `MDL_SSP_SessID` if present) — either as
   `MoodleSession=abc; MDL_SSP_SessID=def` or by pasting the tab-separated rows.
4. Run:

```powershell
coursectl login --cookie "MoodleSession=paste-here; MDL_SSP_SessID=paste-here"
```

The server takes those cookies, asks Moodle's `launch.php` for a token — which with an
authenticated session is granted immediately, no SSO and no Duo — and stores it
encrypted. **The cookies are not stored**; only the resulting Moodle token is.

Over HTTP, the same thing is available to remote clients:

```powershell
curl -X POST http://127.0.0.1:8787/v1/auth/login ^
  -H "Content-Type: application/json" ^
  -d "{\"cookie\": \"MoodleSession=…; MDL_SSP_SessID=…\"}"
```

Why this works: once a session is authenticated, calling
`/admin/tool/mobile/launch.php?service=moodle_mobile_app` returns
`moodlemobile://token=…` directly. Moodle only redirects to SSO when the session is
*not* authenticated — which is also how the server detects invalid cookies
(`session_not_authenticated`).

If York ever puts a JavaScript or MFA step in the way, `--strategy mobile_sso`
still works as long as the challenge is a plain form; otherwise point
`COURSECTL_MAX_REDIRECTS` up and read the SSO trace from `--verbose`.

## What you get

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/auth/login` | Passport York sign-in → API key |
| `GET /v1/me` | Identity + site info |
| `GET /v1/courses` | Enrolled courses |
| `GET /v1/courses/{c}/contents` | Sections, modules, file listings |
| `GET /v1/courses/{c}/files` | Downloadable files with URLs |
| `GET /v1/files?q=lecture` | Search files across all courses |
| `POST /v1/files/download` | Pull a file onto the server's disk |
| `GET /v1/calendar?days=30` | Upcoming eClass events |
| `GET /v1/calendar.ics?days=90` | iCalendar export |
| `GET /v1/deadlines?days=14` | Assignments/quizzes coming due |
| `GET /v1/assignments` | All assignments with due dates |
| `GET /v1/courses/{c}/grades` | Grade items + running total |
| `GET /v1/announcements` | News forums |
| `POST /v1/auth/logout` | Revoke key + forget token |

`{c}` accepts a numeric course id, a shortname (`EECS1012`), or a case-insensitive
substring of the full name.

Interactive docs: `http://127.0.0.1:8787/docs`.
Full inventory of every route and MCP tool: [docs/API-REFERENCE.md](docs/API-REFERENCE.md).

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[dev]"
```

For `coursectl login --browser` you also need Playwright's Chromium, which installs into
*your* cache (it is not shipped with this project):

```powershell
pip install playwright
playwright install chromium
```

## Use

### 1. Sign in once

York uses Duo, so the browser or cookie route is the reliable one:

```powershell
coursectl login --browser
# or
coursectl login --cookie "MoodleSession=…; MDL_SSP_SessID=…"
```

`coursectl login <netid>` (password prompt) still works on Moodle sites without MFA, and
against York it will stop at the Duo challenge with a `mfa_required` error rather than a
misleading "wrong password". See [Duo two-factor authentication](#duo-two-factor-authentication).

```
✓ Signed in
  account      abc123
  display name Doe, Jane
  moodle user  123456
  site         https://eclass.yorku.ca

  API key      ck_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

Add `--verbose` to print the SSO breadcrumb trail if something looks off.

### 2. Run the API

```powershell
coursectl serve --port 8787
```

```powershell
curl -H "Authorization: Bearer ck_…" http://127.0.0.1:8787/v1/courses
curl -H "Authorization: Bearer ck_…" "http://127.0.0.1:8787/v1/deadlines?days=7"
curl -H "Authorization: Bearer ck_…" -o coursectl.ics http://127.0.0.1:8787/v1/calendar.ics
```

### 3. Point an MCP client at it

```powershell
coursectl mcp --account abc123
```

Register it with your harness (stdio transport). Example for a JSON-based client:

```json
{
  "mcpServers": {
    "coursectl": {
      "command": "coursectl",
      "args": ["mcp", "--account", "abc123"]
    }
  }
}
```

MCP tools: `whoami`, `list_courses`, `get_course_contents`, `list_course_files`,
`search_files`, `download_file`, `get_upcoming_deadlines`, `get_calendar`,
`export_calendar`, `get_assignments`, `get_grades`, `get_announcements`,
`get_announcement_posts`.

Ask it things like *"what's due this week"*, *"download the latest lecture PDF for
EECS1012"*, or *"export my calendar"*.

Over HTTP instead of stdio:

```powershell
coursectl mcp --account abc123 --http --port 8790
```

## Architecture

```
src/coursectl/
  config.py      settings (COURSECTL_* env / .env)
  auth.py        Passport York + Shibboleth SSO → Moodle token
  ws.py          Moodle REST Web Services client, param encoding, file streaming
  client.py      domain layer: courses, contents, files, deadlines, grades, ICS
  service.py     account lifecycle: sign in, encrypt, resolve API keys
  store.py       SQLite accounts + audit events
  security.py    Fernet token encryption, API-key hashing
  htmlforms.py   minimal HTML form parser for the SSO pages
  api/routes.py  FastAPI app
  mcp_server.py  MCP tools (thin wrappers over client.py)
  cli.py         login / serve / mcp / accounts / revoke / check
```

The REST API and the MCP server share `client.py`, so behaviour can never drift
between them.

## Security model

- **Passwords are never persisted.** They exist only as a local variable during one
  SSO round-trip.
- **Tokens are encrypted at rest** with Fernet (AES-128-CBC + HMAC-SHA256). The key
  lives in `data/fernet.key` (created with mode `0600`). Losing it means re-login.
- **API keys are stored only as SHA-256 hashes**, so a database leak does not yield
  working credentials. The full key is shown exactly once, at login.
- **Re-login rotates the key** for that account; the previous key stops working.
- **Downloads are host-restricted** to the configured eClass host, which prevents the
  download endpoint from being used as an SSRF primitive.
- `POST /v1/auth/logout` and `coursectl revoke` both wipe the stored token and key.

Hardening switches:

```ini
COURSECTL_ALLOW_OPEN_REGISTRATION=false   # only pre-provisioned accounts may log in
COURSECTL_REGISTRATION_SECRET=…           # shared secret needed by /v1/auth/login
COURSECTL_ADMIN_TOKEN=…                   # enables /v1/admin/*
COURSECTL_CORS_ORIGINS=["https://myapp.example"]
```

## Configuration

Copy `.env.example` to `.env`. Everything is optional except the defaults already
pointing at York.

| Variable | Default | Meaning |
| --- | --- | --- |
| `COURSECTL_SITE_URL` | `https://eclass.yorku.ca` | Moodle base URL |
| `COURSECTL_SERVICE` | `moodle_mobile_app` | Web-service shortname |
| `COURSECTL_HOST` / `COURSECTL_PORT` | `127.0.0.1` / `8787` | Bind address |
| `COURSECTL_DATABASE_PATH` | `data/coursectl.db` | SQLite file |
| `COURSECTL_DOWNLOAD_DIR` | `data/downloads` | Where files land |
| `COURSECTL_REQUEST_TIMEOUT` | `45` | Upstream timeout (s) |
| `COURSECTL_MCP_ACCOUNT` | — | Default account for the MCP server |

## Limits and caveats

- **One token per account.** eClass may invalidate older tokens when you sign in from
  another device; if you see `session_expired`, run `coursectl login` again.
- **Token lifetime is set by York.** Mobile app tokens normally do not expire, but an
  administrator can change `tokenduration`. There is no refresh endpoint, so recovery
  always means a fresh `coursectl login`.
- **`/v1/grades` needs the grade-report function** in the `moodle_mobile_app` service.
  Where it is absent you get a clear `upstream_error` rather than a crash.
- **Respect York's acceptable-use policy** and the course material's licensing. This
  is an unofficial client; it is not affiliated with or endorsed by York University.
  Do not use it to redistribute course content.

## Publishing this as a local tool

The intended distribution model is a **local download you run yourself** — like
`ollama`, `yt-dlp`, or any interop CLI. That posture is what keeps the legal surface
small: you never take custody of anyone's credentials, and each user talks to York
directly with their own account from their own IP.

Before you publish:

1. **Rename it.** `coursectl` and `moodle-*` are trademark landmines for a public repo
   or PyPI package. Pick something neutral.
2. **Set your real User-Agent** — `COURSECTL_USER_AGENT`, currently a
   `github.com/OWNER/...` placeholder. Being identifiable is deliberate and polite.
3. **Don't bundle Playwright browsers.** They download to the user's own cache, which
   keeps their licences their problem. See `THIRD-PARTY-NOTICES.md`.
4. **No real course content** in tests, screenshots, or issues. Use fabricated fixtures.
5. Keep the banner at the top of this README, and ship `LICENSE` (fill in your name) plus
   `SECURITY.md`.

Full analysis, including residual risks and a release checklist:
[docs/LEGAL-AND-RISK.md](docs/LEGAL-AND-RISK.md).

## Status

Validated against the live York services on 2026-11-02:

| Check | Result |
| --- | --- |
| `/login/token.php` reachable, JSON errors | ✅ `invalidlogin` |
| SSO hop 1 → 4 (Shibboleth + LoginHandler CSRF form) | ✅ |
| SSO hop 5 → 7 (`Authn/RemoteUser` → Passport York) | ✅ |
| Credential form fields parsed (`mli`, `password`, `__alb*`, `__pybpp__`) | ✅ |
| Live credential POST reaches Passport York and is answered | ✅ `Login Incorrect` for a bogus account |
| **Duo is enforced after the password** | ⚠️ password-only login cannot complete |
| Duo is detected, never misreported as a bad password | ✅ `mfa_required` |
| `launch.php` refuses unauthenticated cookies and says so | ✅ `session_not_authenticated` |
| `launch.php` grants a token from an authenticated session | ✅ mechanism verified; needs your session |
| `webservice/rest/server.php` + `lib/ajax/service.php` enabled | ✅ |
| Playwright launches Chromium and reaches eClass | ✅ v153.0.8010.12 |
| 88 unit tests, mocked upstream | ✅ |
| HTTP API surface, MCP stdio handshake (13 tools) | ✅ |

The final token exchange needs **your** authenticated session, because Duo is a second
factor. Use `coursectl login --browser` or `coursectl login --cookie` (see
[Duo two-factor authentication](#duo-two-factor-authentication)):

```powershell
coursectl login --browser --verbose
```

Expect the trace to end at `Moodle accepted the session and returned a token`, followed
by your API key. If it stops anywhere else, the error carries the page title, visible
text and the last hops.

To re-check the whole chain without credentials:

```powershell
.\.venv\Scripts\python.exe recon\live_check.py
```

## Development

```powershell
pytest                          # unit tests (no network)
coursectl check --account abc123   # live smoke test against eClass
python recon/mcp_probe.py       # MCP handshake + tool listing
node recon/follow.mjs           # re-probe the SSO chain if York changes it
```

Recon scripts in `recon/` were used to map the flow and are kept so the mapping can be
re-verified cheaply when York changes their login page.
