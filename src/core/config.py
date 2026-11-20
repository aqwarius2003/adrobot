import os
from urllib.parse import urlparse

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def mask_secret(value: str | None) -> str:
    """Safely mask secret strings for logs or debug responses."""
    if not value:
        return ""
    if len(value) <= 6:
        return "***"
    return f"{value[:3]}...{value[-3:]}"


def normalize_tracker_url(raw_url: str) -> str:
    """Strip trailing slashes, /admin, /admin_api/v1 if accidentally included."""
    if not raw_url:
        return ""
    url = raw_url.strip().rstrip("/")
    if url.endswith("/admin_api/v1"):
        url = url[: -len("/admin_api/v1")]
    elif url.endswith("/admin"):
        url = url[: -len("/admin")]
    return url.rstrip("/")


def validate_database_isolation(real_url: str, demo_url: str) -> None:
    """Verify that real and demo modes point to physically isolated databases."""
    if not real_url or not demo_url:
        return
    if real_url.strip() == demo_url.strip():
        raise ValueError(
            f"REAL_DATABASE_URL and DEMO_DATABASE_URL cannot be identical ('{real_url}')"
        )
    if real_url.startswith("sqlite:///") and demo_url.startswith("sqlite:///"):
        p1 = real_url.replace("sqlite:///", "")
        p2 = demo_url.replace("sqlite:///", "")
        if p1 != ":memory:" and p2 != ":memory:":
            abs1 = os.path.abspath(p1)
            abs2 = os.path.abspath(p2)
            if abs1 == abs2:
                raise ValueError(
                    f"REAL_DATABASE_URL and DEMO_DATABASE_URL point to the same physical SQLite file: {abs1}"
                )


def validate_upstream_isolation(real_url: str, demo_url: str) -> None:
    """Verify that demo mode does not accidentally target the live Keitaro tracker."""
    c_real = normalize_tracker_url(real_url)
    c_demo = normalize_tracker_url(demo_url)
    if c_real and c_demo and c_real == c_demo and "tracker.example" not in c_real:
        raise ValueError(
            f"DEMO tracker upstream ({c_demo}) cannot point to the live REAL Keitaro tracker ({c_real})"
        )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Optional development tools are never initialized by a normal launch.
    DEMO_ENABLED: bool = Field(default=False)

    # Tracker connection mode: 'real' (live Keitaro) or 'demo' (autonomous simulator)
    KT_MODE: str = Field(
        default="real",
        description="Keitaro tracker mode: 'real' (live Keitaro tracker) or 'demo' (autonomous simulator)",
    )
    DEMO_SIMULATOR_URL: str = Field(
        default="http://127.0.0.1:8001",
        description="URL of the autonomous Keitaro simulator service in demo mode",
    )

    @field_validator("KT_MODE", mode="before")
    @classmethod
    def validate_kt_mode(cls, v: str, info: ValidationInfo) -> str:
        if not info.data.get("DEMO_ENABLED", False):
            return "real"
        if not isinstance(v, str):
            raise ValueError("KT_MODE must be a string")
        clean = v.strip().lower()
        if clean not in ("real", "demo"):
            raise ValueError(f"Invalid KT_MODE '{v}'. Must be either 'real' or 'demo'.")
        return clean

    # Keitaro settings
    KEITARO_BASE_URL: str = Field(
        default="https://tracker.example",
        description="Base URL of Keitaro tracker (without /admin or /admin_api/v1)",
    )
    KEITARO_API_KEY: str = Field(
        default="",
        description="Keitaro Admin API key",
    )
    REPORT_TIMEZONE: str = Field(
        default="UTC",
        description="Timezone used for Keitaro analytics reports",
    )
    CAMPAIGN_SURNAME: str = Field(
        default="rykov",
        description="Surname used for campaign name prefix: test_<surname>_...",
    )

    # Optional defaults for Creator
    DEFAULT_DOMAIN_ID: int | None = None
    DEFAULT_GROUP_ID: int | str | None = None
    DEFAULT_TRAFFIC_SOURCE_ID: int | None = None

    # Application settings
    APP_ENV: str = Field(default="development")
    APP_HOST: str = Field(default="127.0.0.1")
    APP_PORT: int = Field(default=8000)
    DATABASE_URL: str = Field(
        default="sqlite:///./data/app.db",
        description="Default database connection URL (legacy)",
    )
    REAL_DATABASE_URL: str = Field(
        default="sqlite:///./data/app.db",
        description="Isolated database URL for real Keitaro mode",
    )
    DEMO_DATABASE_URL: str = Field(
        default="sqlite:///./data/demo_app.db",
        description="Isolated database URL for demo mode",
    )
    REAL_KEITARO_BASE_URL: str | None = Field(
        default=None,
        description="Base URL of live Keitaro tracker",
    )
    REAL_KEITARO_API_KEY: str | None = Field(
        default=None,
        description="API key for live Keitaro tracker",
    )
    DEMO_KEITARO_BASE_URL: str | None = Field(
        default=None,
        description="Base URL of demo simulator tracker",
    )
    DEMO_KEITARO_API_KEY: str = Field(
        default="demo-api-key",
        description="Synthetic API key for demo simulator tracker",
    )
    APP_SECRET_KEY: str = Field(default="dev-secret-key-change-in-prod")
    SECURITY_DATABASE_URL: str = Field(
        default="sqlite:///./data/security.db",
        description="Database URL for security store (sessions and rate limits)",
    )
    TRUSTED_PROXIES: list[str] = Field(
        default=["127.0.0.1", "::1"],
        description="Trusted reverse proxy IP addresses for X-Forwarded-For parsing",
    )
    CORS_ORIGINS: list[str] = Field(
        default=[],
        description="Allowed CORS origins list (empty means strict same-origin)",
    )
    SESSION_IDLE_MINUTES: int = Field(default=30)
    SESSION_MAX_LIFETIME_HOURS: int = Field(default=8)
    COMMIT_SHA: str = Field(default="dev")
    APP_VERSION: str = Field(default="0.1.0")

    def get_real_database_url(self) -> str:
        if self.REAL_DATABASE_URL:
            return self.REAL_DATABASE_URL
        return self.DATABASE_URL

    def get_demo_database_url(self) -> str:
        if self.DEMO_DATABASE_URL:
            return self.DEMO_DATABASE_URL
        if "app.db" in self.DATABASE_URL:
            return self.DATABASE_URL.replace("app.db", "demo_app.db")
        if self.DATABASE_URL.startswith("sqlite:///"):
            return self.DATABASE_URL.replace(".db", "_demo.db")
        return f"{self.DATABASE_URL}_demo"

    def get_security_database_url(self) -> str:
        return self.SECURITY_DATABASE_URL

    def get_real_keitaro_base_url(self) -> str:
        return self.REAL_KEITARO_BASE_URL or self.KEITARO_BASE_URL

    def get_real_keitaro_api_key(self) -> str:
        return (
            self.REAL_KEITARO_API_KEY
            if self.REAL_KEITARO_API_KEY is not None
            else self.KEITARO_API_KEY
        )

    def get_demo_keitaro_base_url(self) -> str:
        return self.DEMO_KEITARO_BASE_URL or self.DEMO_SIMULATOR_URL

    def get_demo_keitaro_api_key(self) -> str:
        return self.DEMO_KEITARO_API_KEY

    def is_real_mode_ready(self) -> tuple[bool, str]:
        """Verify readiness of real mode.

        In production, requires safe runtime APP_SECRET_KEY (!= dev default and length >= 32),
        valid REAL_KEITARO_BASE_URL (not tracker.example). Users supply their own API key at login.
        If unconfigured/insecure, real mode is rejected while demo mode remains accessible.
        """
        if self.APP_ENV == "production":
            if (
                not self.APP_SECRET_KEY
                or self.APP_SECRET_KEY == "dev-secret-key-change-in-prod"
                or len(self.APP_SECRET_KEY.strip()) < 32
            ):
                return False, "Insecure or default APP_SECRET_KEY in production"

        url = self.get_real_keitaro_base_url()

        if not url or "tracker.example" in url:
            return False, "REAL_KEITARO_BASE_URL is not configured"
        try:
            parsed = urlparse(normalize_tracker_url(url))
            if (
                not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                return False, "Invalid tracker URL"
            if parsed.scheme not in ("http", "https"):
                return False, "Invalid tracker URL scheme"
            if self.APP_ENV == "production" and parsed.scheme != "https":
                return False, "HTTPS tracker URL is required in production"
        except ValueError:
            return False, "Invalid tracker URL"

        return True, "Ready"

    @property
    def is_demo_mode(self) -> bool:
        return self.DEMO_ENABLED and self.KT_MODE.strip().lower() == "demo"

    @property
    def default_mode(self) -> str:
        return "demo" if self.is_demo_mode else "real"

    @property
    def effective_api_key(self) -> str:
        # In demo mode, NEVER send real Keitaro credentials
        if self.is_demo_mode:
            return "demo-api-key"
        return self.KEITARO_API_KEY

    @property
    def effective_database_url(self) -> str:
        # Maintain strictly isolated database for demo mode across any URL configuration
        if self.is_demo_mode:
            if "app.db" in self.DATABASE_URL:
                return self.DATABASE_URL.replace("app.db", "demo_app.db")
            if self.DATABASE_URL.startswith("sqlite:///"):
                if "_demo.db" not in self.DATABASE_URL:
                    return self.DATABASE_URL.replace(".db", "_demo.db")
            elif not self.DATABASE_URL.endswith("_demo"):
                return f"{self.DATABASE_URL}_demo"
        return self.DATABASE_URL

    @property
    def is_keitaro_configured(self) -> bool:
        if self.is_demo_mode:
            return True
        return self.is_real_mode_ready()[0]

    @property
    def normalized_base_url(self) -> str:
        """Strip trailing slashes, /admin, /admin_api/v1 if accidentally included."""
        target_url = self.DEMO_SIMULATOR_URL if self.is_demo_mode else self.KEITARO_BASE_URL
        url = target_url.strip().rstrip("/")
        if url.endswith("/admin_api/v1"):
            url = url[: -len("/admin_api/v1")]
        elif url.endswith("/admin"):
            url = url[: -len("/admin")]
        return url.rstrip("/")


settings = Settings()
