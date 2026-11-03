# Legal and policy risk — read before publishing

> I am not a lawyer, and this is not legal advice. It is an honest risk analysis to help
> you decide how to release this. If you plan to host it for other people, take money
> for it, or build a company on it, talk to an actual lawyer (and to York).

## The Ollama model: releasing this as a local download

This is the intended posture, and it is by far the strongest one. The analogy is
accurate in the way that matters:

> Ollama ships code, runs on your machine, talks to servers directly from your IP with
> your own account, and takes no custody of anyone's data. Ollama is not liable for the
> licences of the models *you* download. Same structure here — except this project
> doesn't even distribute third-party content, only code.

### Why this posture is so much safer

**1. You are a software author, not a service operator.**
There is no intermediary. Each user's machine talks to `eclass.yorku.ca` directly, from
their own IP, with their own account. You never see, transmit, store or process anyone
else's credentials, sessions, grades or course material. That single fact removes:

- credential-handling and custodianship duties
- PIPEDA obligations (you are not collecting personal information — no "commercial
  activity" and no dataset)
- breach-notification exposure (there is no central store to breach)
- the "this looks like phishing" problem entirely

**2. Your liability is limited to the software itself.**
If the tool deletes someone's downloads folder or gets their account flagged, that is a
claim about your code, not about your trustworthiness as a custodian. A permissive
licence with a warranty disclaimer (MIT's `AS IS`, no warranty, no liability for damages)
is the standard shield — and because you are giving it away for free, there is no sale,
no consumer contract, and Canadian courts are reluctant to impose duties of care on
free software authors absent gross negligence or wilful misconduct. Not absolute, but
this is the same footing every open-source CLI tool stands on.

**3. The user, not you, is party to York's terms.**
Each person who runs it accepts their own institution's acceptable-use policy. Your
software has substantial legitimate uses (it is an interoperability client for a
documented API, like the official app), so it is not itself an unlawful tool. This is
the classic position that interop clients occupy.

**4. Nothing to take down.**
The realistic institutional response to an *official-API interop client* is a polite
email or a package-name trademark complaint — not litigation. There is no hosted service
to shut down and no content being redistributed.

### What residual risk actually remains

| Risk | Likelihood | Severity | Mitigation |
| --- | --- | --- | --- |
| **Trademark complaint** over the name ("eClass", "Moodle") | Medium — this is now your top risk | Low (rename) | Neutral name, non-affiliation notice |
| PyPI/npm **rejects or transfers** the package name as infringing | Medium | Low | Don't use `eclass`/`moodle` in the package name |
| User's account gets flagged by York | Medium (for the user, not you) | Low–medium | Warn them; low request volume; identifiable UA |
| Polite "please stop distributing" email | Low | Low | Comply quickly; keep it local-only |
| Copyright complaint over **screenshots/demo** in your repo | Low–medium — easy to avoid, easy to trigger | Medium | Never screenshot real course material; use dummy data |
| Claim your code damaged someone | Very low | Low | MIT warranty disclaimer |
| Criminal/civil issue if MFA were automated | Would be **your** exposure, not the user's | High | **Never automate Duo**; keep human approval |

### The three things that could still bite you

1. **The name.** This is the single most likely complaint. `coursectl` as a PyPI
   package or a repo title is asking for a trademark notice. Rename to something
   neutral, and put "unofficial, not affiliated with York University or Moodle Pty Ltd"
   in the README and the package description.
2. **Screenshots and demo data.** Never commit or screenshot real lecture slides, real
   grades, real announcements, or real exam schedules. That is you redistributing
   third-party copyrighted material in your repo. Use fabricated fixture data.
3. **Automating MFA.** The moment anyone adds automated Duo solving, the framing changes
   from "interoperability client" to "circumventing an access control" — and that
   exposure would sit with you as the author, not just the user. Keep the human in the
   loop and say why in the README.

### Practical checklist for a local release

- [ ] Neutral project name; no York/Moodle marks in the name, logo, or package id
- [ ] README banner: *unofficial, local-only, no telemetry, use at your own risk, may
      violate your institution's terms*
- [ ] `LICENSE` = **pure**, verbatim MIT (or Apache-2.0 for an explicit patent grant) —
      do not append extra restrictions to it, or the `MIT` metadata becomes untrue;
      put the unofficial-use terms in `NOTICE.md` instead
- [ ] `THIRD-PARTY-NOTICES.md` — all deps are permissive (see the file); don't bundle
      Playwright browser binaries
- [ ] No telemetry, no analytics, no phone-home — keep it genuinely true
- [ ] No real course content in tests, screenshots, issues, or docs
- [ ] Credentials never stored; tokens encrypted; cookies never persisted (already true)
- [ ] MFA stays human-in-the-loop
- [ ] Set `COURSECTL_USER_AGENT` to your real project URL before publishing
- [ ] Localhost binding by default (already `127.0.0.1`)
- [ ] `SECURITY.md` with a private reporting address
- [ ] Optional but kind: a heads-up to York UIT / the eClass team

### Bottom line for a local tool

You are shipping a free, self-contained CLI that talks to a documented API from the
user's own machine with the user's own account. That is ordinary open-source software.
Your realistic obligations are: license your code properly, don't use someone else's
trademark, don't redistribute their content, don't host other people's credentials, and
don't automate the second factor. Get those right and the legal surface is small — the
same surface Ollama, `yt-dlp`, or any interop client lives with.

---

## What this software technically does — and why that matters

These facts are your strongest defence, so keep them true:

1. **It uses Moodle's documented Web Services API.** `core_course_get_contents`,
   `core_enrol_get_users_courses` and friends are a public, versioned API. Nothing is
   scraped out of HTML to obtain data.
2. **It uses the same token mechanism as the official Moodle app.** `launch.php` exists
   specifically so third-party clients can obtain a token. We are an interoperable
   client, not an intruder.
3. **Credentials are the user's own.** No privilege escalation, no guessing, no access
   to other people's data.
4. **MFA is not bypassed.** A human approves the Duo push. If that ever changes — if
   anyone adds automated Duo solving — the analysis becomes *much* worse, because
   defeating an access control is a different category of problem.
5. **Passwords are never stored.** Only a delegated token, encrypted.

Both Moodle and its mobile app are open source, so this behaviour is public, documented
and intended.

## The single biggest fork: self-hosted vs hosted

| | Self-hosted (user runs it on their own machine) | Hosted service (you run it) |
| --- | --- | --- |
| Who holds the password | The user's own machine | **You** |
| Who is the intermediary | Nobody | You, for every user |
| York policy exposure | User's own account, own actions | You, on behalf of strangers |
| Privacy law | Minimal | PIPEDA applies to commercial activity; you become a custodian of personal data |
| Breach impact | One user | Every user you hold sessions for |
| Looks like | A personal tool | Credential harvesting, to a casual observer |
| Overall | **Low risk** | **High risk — I would not do this** |

**Recommendation: ship it local-only.** The server already binds `127.0.0.1` by
default and never transmits credentials anywhere except to York. Keep it that way, and
say so loudly in the README. Do not offer a hosted demo where people type their
Passport York password into your server.

If you ever *do* want a hosted product, the only sane design is one where you never see
a password: have users create a Moodle mobile token themselves (Site administration →
Preferences → "Manage mobile app access") and paste *that*. York appears to have
`/user/manage_tokens.php` blocked, so this may not be available — which is another
reason to stay self-hosted.

## Likely institutional rules

You are bound by York's computing policies as an account holder. The relevant
instruments are normally:

- **York's Acceptable Use of Computing and Information Resources** policy
- The **eClass / Moodle terms of use** shown at login
- ResNet/UIT guidelines on account sharing and automation

I have not read the current text of these, and you should — they change. Typical clauses
that would apply: no sharing passwords, no unauthorised access, no automated activity
that degrades service, no circumventing security controls. Note that **automating a
login flow is often restricted even when the underlying API is public**, and "we're
using the official API" is not a blanket permission if the institution's terms say
"no automated access".

## Copyright — this is where real legal exposure lives

Course material (lecture slides, PDFs, recorded lectures, exam schedules) belongs to
instructors or York, not to students. Downloading your own course's files for private
study is broadly fine — in Canada, fair dealing for private study/research (Copyright
Act s. 29) covers personal use.

What is **not** fine:

- Redistributing course material, or shipping it in your repo/demo/screenshots
- Any feature that lets one user read another's courses
- A shared cache/server that re-serves downloaded files
- Bundling "sample" PDFs or fixtures from real courses

So: no content sharing, no sample data from real courses, files stay on the user's disk.

## Trademark and naming

- **"eClass" is York's brand name.** Naming your project "eClass API" invites a
  trademark complaint and confuses users into thinking it is official.
- **"Moodle" is a trademark** of Moodle Pty Ltd.

Pick a neutral name (`moodlectl`, `campus-sync`, `notes-harvester`, …), and put a
prominent disclaimer: *"Unofficial. Not affiliated with or endorsed by York University
or Moodle Pty Ltd."* Do not use York's logo, crest, or their purple in marketing.

## Privacy

- **Self-hosted, no telemetry:** you collect nothing, so PIPEDA largely does not bite.
  Do not add analytics that phone home with course names or usage data — that would
  create exactly the database you do not want to be responsible for.
- **Hosted:** you become a processor of personal information and inherit consent,
  safeguard and breach-reporting duties. Ontario universities are also subject to
  FIPPA, and their vendors/projects can end up in scope.
- Logs: never log tokens, cookies, or passwords. (The code redacts `TokenBundle` in
  `repr` and sanitises URLs in traces — keep it that way.)

## Open-source licensing

- **MIT or Apache-2.0** is fine for your code.
- Ship an explicit **warranty disclaimer** (MIT already has one) plus a plain-language
  README warning: *"Use at your own risk. This may violate your institution's terms of
  service. You are responsible for your account."*
- Do **not** include York assets, course content, or scraped data in the repo.
- Don't copy code from Moodle itself (GPL) into an MIT project. You are calling its API,
  not linking its code — that keeps your licence your own.

## How enforcement actually tends to go

Realistically, institutions respond in this order:

| Outcome | Likelihood | Severity |
| --- | --- | --- |
| Nothing happens | Most likely, especially self-hosted and low volume | — |
| Account temporarily locked / flagged | Medium | Annoying, recoverable |
| Your IP or client blocked | Low–medium | Annoying |
| Polite email asking you to stop | Low if self-hosted; medium if you promote a hosted version | Low |
| Cease-and-desist over a public hosted service | Low–medium | High — comply immediately |
| Lawsuit | Very low, unless you redistribute content or cause real harm | — |

Lawsuits over personal-use interop tools are rare. **Account-level enforcement is the
realistic cost**, and a hosted service is what turns "a student built a tool" into
"someone is operating an unauthorised intermediary for our students' credentials".

## The LinkedIn question

Promotion itself is not a legal problem — it is a *visibility* problem. Posting
publicly increases the odds York IT notices and responds. That is fine if you have
framed it correctly:

- Lead with **"runs locally on your machine; your password never leaves your computer"**
- Emphasise **personal-use** and **accessibility/productivity**, not bulk extraction
- Do not advertise scraping, mirroring, or anything that sounds like circumvention
- Have a plan for the "please stop" email: comply fast, keep the code self-hosted-only,

A good move before posting: email the eClass team / UIT and tell them what you built and
why. Many institutions are receptive to interop tools, and a friendly heads-up converts
a potential enforcement event into a conversation. Worst case they say no, and you know.

## Before you check something: the official alternatives

Worth knowing, because it strengthens your pitch rather than undercutting it:

- **Calendar → Google Calendar already has an official path.** Moodle ships
  `/calendar/export.php` with a "Get calendar URL" option producing a live ICS feed you
  can subscribe to in Google Calendar. I confirmed that endpoint exists on York's eClass
  (login required). For that one use case, users do not need your tool — and you should
  say so, because it builds trust.
- **The official Moodle app** already gives mobile access.

So position your project on what genuinely is not available elsewhere: a **unified,
scriptable API** and **agent/MCP access** — "download and organise my lecture notes",
"tell my assistant what's due", "one place for my term". That is a real gap, and it is a
much more sympathetic story than "I made a scraper".

## A safe-release checklist

- [ ] Local-only by default (`127.0.0.1`), documented loudly
- [ ] Passwords never stored, tokens encrypted, cookies never persisted
- [ ] No telemetry, no analytics, no phone-home
- [ ] No content sharing, no shared cache, no sample course data in the repo
- [ ] Neutral project name; no York/Moodle branding
- [ ] `NOTICE` + README: unofficial, unaffiliated, no warranty, use at your own risk
- [ ] MFA stays human-in-the-loop; never automate Duo
- [ ] Identifiable `User-Agent` (already: `COURSECTL_USER_AGENT`) — be a good citizen, not a
      stealth scraper
- [ ] Rate limiting / no bulk enumeration of courses or users
- [ ] `coursectl revoke` and `logout` documented as the off-switch
- [ ] MIT licence with warranty disclaimer
- [ ] Consider a heads-up to York UIT / the eClass team before publicising

## Bottom line

Self-hosted, personal-use, honest-client, no content redistribution, neutral name,
clear disclaimers: this is in the same risk class as any number of community Moodle
tools, and the realistic worst case is an account flag or a polite email.

Hosted service taking other people's Passport York passwords: do not. That is where
the policy violation, the privacy exposure, and the "this looks like phishing" problem
all land at once.
