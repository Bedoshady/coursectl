"""Collect third-party licence metadata for the dependency tree."""

from __future__ import annotations

import sys
from importlib.metadata import distributions

sys.path.insert(0, "src")

DIRECT = {
    "fastapi",
    "uvicorn",
    "httpx",
    "cryptography",
    "pydantic",
    "pydantic-settings",
    "mcp",
    "playwright",
}


def license_of(meta) -> str:
    for key in ("License-Expression", "License"):
        value = meta.get(key)
        if value and value.strip() and value.strip().upper() != "UNKNOWN":
            return " ".join(value.split())[:60]
    classifiers = meta.get_all("Classifier") or []
    for c in classifiers:
        if c.startswith("License ::"):
            return c.split("::")[-1].strip()[:60]
    return "see package"


rows = []
for dist in distributions():
    meta = dist.metadata
    name = meta.get("Name")
    if not name:
        continue
    key = name.lower().replace("_", "-")
    if key not in DIRECT:
        continue
    rows.append((key, dist.version, license_of(meta), meta.get("Home-page") or ""))

for name, version, lic, _home in sorted(rows):
    print(f"{name:<20} {version:<10} {lic}")
print(f"\n{len(rows)} of {len(DIRECT)} direct dependencies found")
