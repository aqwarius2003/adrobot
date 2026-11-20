import logging
import re
import sys

from src.core.config import settings

SECRET_PATTERNS = [
    re.compile(r"(Api-Key:\s*)([^\s,]+)", re.IGNORECASE),
    re.compile(r"(api_key=)([^\s&]+)", re.IGNORECASE),
    re.compile(r"(password=)([^\s&]+)", re.IGNORECASE),
    re.compile(r"(\"api_key\":\s*\")([^\"]+)(\")", re.IGNORECASE),
    re.compile(r"(adrobot_session=)([^\s;]+)", re.IGNORECASE),
    re.compile(r"(X-CSRF-Token:\s*)([^\s,]+)", re.IGNORECASE),
    re.compile(r"(Bearer\s+)([^\s,]+)", re.IGNORECASE),
]


class SecretMaskingFormatter(logging.Formatter):
    """Formatter that strips/masks known API keys, secrets, and session tokens from log records."""

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        masked = original

        # Mask known configured secret strings
        keys_to_mask = [
            settings.get_real_keitaro_api_key(),
            settings.REAL_KEITARO_API_KEY,
            settings.KEITARO_API_KEY,
            settings.APP_SECRET_KEY,
            settings.DEMO_KEITARO_API_KEY,
        ]
        for k in keys_to_mask:
            if k and len(k) > 2 and k != "dev-secret-key-change-in-prod":
                masked = masked.replace(k, "***MASKED_KEY***")

        for pattern in SECRET_PATTERNS:
            if pattern.groups == 3:
                masked = pattern.sub(r"\1***MASKED***\3", masked)
            else:
                masked = pattern.sub(r"\1***MASKED***", masked)
        return masked


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    formatter = SecretMaskingFormatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO if settings.APP_ENV == "production" else logging.DEBUG)
    root_logger.handlers = [handler]


logger = logging.getLogger("adrobot")
