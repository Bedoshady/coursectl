# Third-party notices

This project is distributed under the MIT licence (see `LICENSE`). It depends on the
following packages, all under permissive licences that allow redistribution provided
their notices are preserved.

| Package | Licence | Copyright |
| --- | --- | --- |
| fastapi | MIT | Sebastián Ramírez |
| starlette | BSD-3-Clause | Encode OSS Ltd. |
| pydantic | MIT | Samuel Colvin and contributors |
| pydantic-settings | MIT | Pydantic contributors |
| uvicorn | BSD-3-Clause | Encode OSS Ltd. |
| httpx | BSD-3-Clause | Encode OSS Ltd. |
| httpcore | BSD-3-Clause | Encode OSS Ltd. |
| anyio | MIT | Alex Grönholm |
| cryptography | Apache-2.0 OR BSD-3-Clause | The Python Cryptographic Authority |
| mcp | MIT | Anthropic, PBC |
| playwright (optional) | Apache-2.0 | Microsoft Corporation |

## Browser binaries

`--browser` mode uses Playwright to drive Chromium. **The browsers are not distributed
with this project.** Playwright downloads them on demand into the user's own cache
(`%LOCALAPPDATA%\ms-playwright`, `~/Library/Caches/ms-playwright`, or
`~/.cache/ms-playwright`), and each has its own licence and open-source notices. If you
ever bundle browsers in a release artifact, you must ship Chromium's notices too — the
simplest way to stay compliant is not to bundle them.

## Moodle

This project is an independent client that talks to a Moodle server over its public Web
Services API. It contains no Moodle code. Moodle is free software under the GPLv3, and
its source is available at <https://moodle.org>. "Moodle" is a trademark of Moodle Pty
Ltd. This project is not affiliated with or endorsed by Moodle Pty Ltd.

## York University

"eClass" and "Passport York" are names used by York University. This project is
**unofficial** and is not affiliated with, endorsed by, or supported by York University.
It contains no York University content, logos, or course material.

## Trademark disclaimer

All other trademarks are the property of their respective owners. Use of a name does not
imply any affiliation or endorsement.
