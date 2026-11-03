"""Configuration for coursectl.

All settings are read from environment variables prefixed with ``COURSECTL_`` or
from a ``.env`` file in the project root.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime configuration."""

    model_config = SettingsConfigDict(
        env_prefix="COURSECTL_",
        env_file=(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Upstream sites -------------------------------------------------
    site_url: str = "https://eclass.yorku.ca"
    """Base URL of the eClass (Moodle) instance."""

    service: str = "moodle_mobile_app"
    """Moodle web-service shortname used when minting the token."""

    urlscheme: str = "moodlemobile"
    """URL scheme Moodle redirects to after SSO; carries the token."""

    passport_host: str = "passportyork.yorku.ca"
    """Host that serves the Passport York credential form."""

    # --- Server ---------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8787
    log_level: str = "info"
    cors_origins: list[str] = ["*"]
    request_timeout: float = 45.0
    login_timeout: float = 90.0
    max_redirects: int = 25

    # --- Storage --------------------------------------------------------
    database_path: Path = PROJECT_ROOT / "data" / "coursectl.db"
    secret_key_file: Path = PROJECT_ROOT / "data" / "fernet.key"
    download_dir: Path = PROJECT_ROOT / "data" / "downloads"

    user_agent: str = "coursectl/0.1 (+https://github.com/OWNER/coursectl)"
    """Identifies this client on Web Services traffic.

    Being identifiable is deliberate: it is honest, and it lets an institution
    contact the operator instead of silently blocking an anonymous scraper.
    """

    # --- Access control -------------------------------------------------
    allow_open_registration: bool = True
    """When false, ``/v1/auth/login`` requires the ``COURSECTL_REGISTRATION_SECRET``."""

    registration_secret: str | None = None
    admin_token: str | None = None
    """Bearer token for the ``/v1/admin/*`` routes."""

    # --- MCP ------------------------------------------------------------
    mcp_api_key: str | None = None
    """API key the MCP server should act as (falls back to single-account mode)."""
    mcp_account: str | None = None
    """Passport York username the MCP server should act as."""
    mcp_transport: str = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8790

    @field_validator("site_url", "passport_host")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def launch_path(self) -> str:
        return "/admin/tool/mobile/launch.php"

    @property
    def ws_path(self) -> str:
        return "/webservice/rest/server.php"

    @property
    def token_path(self) -> str:
        return "/login/token.php"

    @property
    def pply_login_url(self) -> str:
        return f"https://{self.passport_host}/ppylogin/ppylogin"

    def ensure_dirs(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.secret_key_file.parent.mkdir(parents=True, exist_ok=True)
        self.download_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
