from pydantic import Field
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
    def is_keitaro_configured(self) -> bool:
        return bool(
            self.KEITARO_API_KEY
            and self.KEITARO_BASE_URL
            and "tracker.example" not in self.KEITARO_BASE_URL
        )

    @property
    def normalized_base_url(self) -> str:
        """Strip trailing slashes, /admin, /admin_api/v1 if accidentally included."""
        url = self.KEITARO_BASE_URL.strip().rstrip("/")
        if url.endswith("/admin_api/v1"):
            url = url[: -len("/admin_api/v1")]
        elif url.endswith("/admin"):
            url = url[: -len("/admin")]
        return url.rstrip("/")


settings = Settings()
