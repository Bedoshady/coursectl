"""Demonstrate how the event timestamps read — raw UTC vs local, old vs new."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from coursectl.cli import _local_time
from coursectl.client import _event_summary

RAW = [
    ("2026-11-06T15:00:00+00:00", "Quiz 11 closes"),
    ("2026-11-06T15:00:00+00:00", "Lecture 14 MMP is due"),
    ("2026-11-07T03:10:00+00:00", "Regrading Requests - Programming Test (F&#39;;26) closes"),
    ("2026-11-07T04:55:00+00:00", "Group Concept Submission is due"),
    ("2026-11-09T15:00:00+00:00", "Quiz 14 closes"),
]


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

    print("BEFORE (what you saw: raw UTC + leaked HTML entity)\n")
    for iso, name in RAW:
        print(f"  {iso}  {name}")

    print("\nAFTER (local time, entities decoded)\n")
    for iso, name in RAW:
        epoch = int(datetime.fromisoformat(iso).timestamp())
        summary = _event_summary({"id": 1, "name": name, "timestart": epoch})
        when = _local_time(summary["start"])
        print(f"  {when}  {summary['name']}")

    print("\nLocal zone:", datetime.now().astimezone().tzinfo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
