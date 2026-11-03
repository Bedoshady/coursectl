"""Secret handling: symmetric encryption for stored Moodle tokens, and API keys.

Design notes
------------
* Passwords are **never** persisted. We keep only the delegated Moodle web-service
  token, encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256).
* Callers get an opaque ``ek_...`` API key. Only its SHA-256 hash is stored, so a
  database leak does not hand out working credentials.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .errors import Unauthorized

API_KEY_PREFIX = "ek_"
API_KEY_BYTES = 32


class SecretBox:
    """Thin wrapper around :class:`cryptography.fernet.Fernet`."""

    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    @classmethod
    def load_or_create(cls, path: Path) -> "SecretBox":
        """Read the key at ``path``, creating it with restrictive permissions if absent."""
        if path.exists():
            raw = path.read_bytes().strip()
            if not raw:
                raise ValueError(f"empty encryption key file: {path}")
            return cls(raw)

        path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        # Create exclusively so two processes cannot race and clobber a live key.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, key)
        finally:
            os.close(fd)
        return cls(key)

    def encrypt(self, plaintext: str | None) -> str | None:
        if plaintext is None:
            return None
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str | None) -> str | None:
        if token is None:
            return None
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:  # key rotated or ciphertext corrupted
            raise Unauthorized(
                "Stored credential could not be decrypted; re-authenticate."
            ) from exc


def generate_api_key() -> tuple[str, str, str]:
    """Return ``(plaintext, sha256_hex, display_prefix)`` for a brand new API key."""
    body = secrets.token_urlsafe(API_KEY_BYTES).replace("-", "").replace("_", "")[:40]
    plaintext = f"{API_KEY_PREFIX}{body}"
    return plaintext, hash_api_key(plaintext), f"{plaintext[:11]}…"


def hash_api_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def new_id() -> str:
    """Short, URL-safe identifier for accounts."""
    return base64.urlsafe_b64encode(os.urandom(12)).decode().rstrip("=")
