import logging
import re
import sys

from src.core.config import settings

SECRET_PATTERNS = [
    re.compile(r"(Api-Key:\s*)([^\s,]+)", re.IGNORECASE),
    re.compile(r"(api_key=)([^\s&]+)", re.IGNORECASE),
    re.compile(r"(password=)([^\s&]+)", re.IGNORECASE),
]


class SecretMaskingFormatter(logging.Formatter):
    """Formatter that strips/masks known API keys and tokens from log records."""

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        masked = original
        if settings.KEITARO_API_KEY:
            masked = masked.replace(settings.KEITARO_API_KEY, "***MASKED_KEY***")
        for pattern in SECRET_PATTERNS:
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
