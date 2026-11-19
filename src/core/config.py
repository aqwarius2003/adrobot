from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def mask_secret(value: str | None) -> str:
    """Safely mask secret strings for logs or debug responses."""
    if not value:
        return ""
    if len(value) <= 6:
        return "***"
    return f"{value[:3]}...{value[-3:]}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Tracker connection mode: 'real' (live Keitaro) or 'demo' (autonomous simulator)
    KT_MODE: str = Field(
        default="real",
        description="Keitaro tracker mode: 'real' (live Keitaro tracker) or 'demo' (autonomous simulator)",
    )
    DEMO_SIMULATOR_URL: str = Field(
        default="http://127.0.0.1:8001",
        description="URL of the autonomous Keitaro simulator service in demo mode",
    )
    REAL_APP_URL: str | None = Field(
        default=None,
        description="URL of the real mode app instance, e.g. http://localhost:8000",
    )
    DEMO_APP_URL: str | None = Field(
        default=None,
        description="URL of the demo mode app instance, e.g. http://localhost:8002",
    )

    @field_validator("KT_MODE")
    @classmethod
    def validate_kt_mode(cls, v: str) -> str:
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
    CAMPAIGN_SURNAME: str = Field(
        default="novikov",
        description="Surname used for campaign name prefix: test_<surname>_...",
    )

    # Optional defaults for Creator
    DEFAULT_DOMAIN_ID: int | None = None
    DEFAULT_GROUP_ID: int | str | None = None
    DEFAULT_TRAFFIC_SOURCE_ID: int | None = None

    # Application settings
    APP_ENV: str = Field(default="development")
    APP_HOST: str = Field(default="0.0.0.0")
    APP_PORT: int = Field(default=8000)
    DATABASE_URL: str = Field(
        default="sqlite:///./data/app.db",
        description="Database connection URL",
    )
    APP_SECRET_KEY: str = Field(default="dev-secret-key-change-in-prod")
    COMMIT_SHA: str = Field(default="dev")
    APP_VERSION: str = Field(default="0.1.0")

    @property
    def is_demo_mode(self) -> bool:
        return self.KT_MODE.strip().lower() == "demo"

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
        return bool(
            self.KEITARO_API_KEY
            and self.KEITARO_BASE_URL
            and "tracker.example" not in self.KEITARO_BASE_URL
        )

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
