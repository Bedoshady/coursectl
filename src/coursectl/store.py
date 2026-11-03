"""SQLite persistence for accounts and audit events.

We keep this deliberately small: one connection per operation, WAL mode, and no
ORM. All token material stored here is already encrypted by :mod:`coursectl.security`.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .security import new_id

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id                TEXT PRIMARY KEY,
    username          TEXT NOT NULL UNIQUE,
    display_name      TEXT,
    email             TEXT,
    moodle_user_id    INTEGER,
    site_url          TEXT NOT NULL,
    token_enc         TEXT NOT NULL,
    private_token_enc TEXT,
    api_key_hash      TEXT NOT NULL UNIQUE,
    api_key_prefix    TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    last_login_at     TEXT,
    last_used_at      TEXT,
    revoked_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_accounts_api_key ON accounts(api_key_hash);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT,
    kind       TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Account:
    id: str
    username: str
    display_name: str | None
    email: str | None
    moodle_user_id: int | None
    site_url: str
    token_enc: str
    private_token_enc: str | None
    api_key_hash: str
    api_key_prefix: str
    created_at: str
    last_login_at: str | None
    last_used_at: str | None
    revoked_at: str | None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def public(self, *, token: str | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "email": self.email,
            "moodle_user_id": self.moodle_user_id,
            "site_url": self.site_url,
            "api_key_prefix": self.api_key_prefix,
            "created_at": self.created_at,
            "last_login_at": self.last_login_at,
            "last_used_at": self.last_used_at,
        }
        if token is not None:
            data["api_key"] = token
        return data


class Store:
    """Account and event storage."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
        finally:
            conn.close()

    # -- accounts --------------------------------------------------------
    def upsert_account(
        self,
        *,
        username: str,
        site_url: str,
        token_enc: str,
        private_token_enc: str | None,
        api_key_hash: str,
        api_key_prefix: str,
        display_name: str | None = None,
        email: str | None = None,
        moodle_user_id: int | None = None,
    ) -> Account:
        """Create the account, or rotate its token + API key if it already exists."""
        now = utcnow()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM accounts WHERE username = ?", (username,)
            ).fetchone()
            if row is None:
                account_id = new_id()
                conn.execute(
                    """
                    INSERT INTO accounts (id, username, display_name, email, moodle_user_id,
                        site_url, token_enc, private_token_enc, api_key_hash, api_key_prefix,
                        created_at, last_login_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        account_id, username, display_name, email, moodle_user_id,
                        site_url, token_enc, private_token_enc, api_key_hash,
                        api_key_prefix, now, now,
                    ),
                )
            else:
                account_id = row["id"]
                conn.execute(
                    """
                    UPDATE accounts
                       SET display_name      = COALESCE(?, display_name),
                           email             = COALESCE(?, email),
                           moodle_user_id    = COALESCE(?, moodle_user_id),
                           site_url          = ?,
                           token_enc         = ?,
                           private_token_enc = ?,
                           api_key_hash      = ?,
                           api_key_prefix    = ?,
                           last_login_at     = ?,
                           revoked_at        = NULL
                     WHERE id = ?
                    """,
                    (
                        display_name, email, moodle_user_id, site_url, token_enc,
                        private_token_enc, api_key_hash, api_key_prefix, now, account_id,
                    ),
                )
        account = self.get_account_by_id(account_id)
        assert account is not None
        return account

    def get_account_by_id(self, account_id: str) -> Account | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return _row_to_account(row)

    def get_account_by_username(self, username: str) -> Account | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM accounts WHERE username = ?", (username.lower(),)
            ).fetchone()
        return _row_to_account(row)

    def get_account_by_api_key(self, api_key_hash: str) -> Account | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM accounts WHERE api_key_hash = ?", (api_key_hash,)
            ).fetchone()
        return _row_to_account(row)

    def list_accounts(self) -> list[Account]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM accounts ORDER BY created_at").fetchall()
        return [a for a in (_row_to_account(r) for r in rows) if a is not None]

    def touch_usage(self, account_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE accounts SET last_used_at = ? WHERE id = ?", (utcnow(), account_id)
            )

    def revoke_account(self, account_id: str) -> bool:
        """Invalidate the API key and forget the stored token."""
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE accounts
                   SET revoked_at = ?, token_enc = '', private_token_enc = NULL
                 WHERE id = ? AND revoked_at IS NULL
                """,
                (utcnow(), account_id),
            )
            return cur.rowcount > 0

    # -- events ----------------------------------------------------------
    def log_event(self, kind: str, *, account_id: str | None = None, detail: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO events (account_id, kind, detail, created_at) VALUES (?,?,?,?)",
                (account_id, kind, detail, utcnow()),
            )

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def _row_to_account(row: sqlite3.Row | None) -> Account | None:
    if row is None:
        return None
    return Account(
        id=row["id"],
        username=row["username"],
        display_name=row["display_name"],
        email=row["email"],
        moodle_user_id=row["moodle_user_id"],
        site_url=row["site_url"],
        token_enc=row["token_enc"],
        private_token_enc=row["private_token_enc"],
        api_key_hash=row["api_key_hash"],
        api_key_prefix=row["api_key_prefix"],
        created_at=row["created_at"],
        last_login_at=row["last_login_at"],
        last_used_at=row["last_used_at"],
        revoked_at=row["revoked_at"],
    )
