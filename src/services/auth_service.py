import logging
from typing import Any

import httpx

from src.core.config import normalize_tracker_url, settings
from src.core.security import (
    SecurityStore,
    generate_csrf_token,
    generate_session_token,
    get_key_fingerprint,
    get_security_store,
    hash_token,
)

logger = logging.getLogger(__name__)


class AuthService:
    """Service handling API-key authentication, rate limits, Keitaro read check, and session issuance."""

    def __init__(self, store: SecurityStore | None = None):
        self.store = store or get_security_store()

    async def authenticate(
        self,
        api_key: str | None,
        client_ip: str,
        kt_client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        # 1. Rate limit check (server-side, atomic)
        is_allowed, retry_after = self.store.check_rate_limit(client_ip)
        if not is_allowed:
            return {
                "success": False,
                "status_code": 429,
                "detail": f"Слишком много попыток. Повторите через {retry_after} сек.",
                "retry_after": retry_after,
            }

        # 2. Input sanitization and bounds
        if not api_key or not isinstance(api_key, str):
            tier, retry_after = self.store.record_failed_attempt(client_ip)
            if retry_after > 0:
                return {
                    "success": False,
                    "status_code": 429,
                    "detail": f"Слишком много попыток. Повторите через {retry_after} сек.",
                    "retry_after": retry_after,
                }
            return {
                "success": False,
                "status_code": 401,
                "detail": "API-ключ не подходит или не даёт доступа к кампаниям",
            }

        api_key_clean = api_key.strip()
        if len(api_key_clean) > 1024 or len(api_key_clean) < 3:
            tier, retry_after = self.store.record_failed_attempt(client_ip)
            if retry_after > 0:
                return {
                    "success": False,
                    "status_code": 429,
                    "detail": f"Слишком много попыток. Повторите через {retry_after} сек.",
                    "retry_after": retry_after,
                }
            return {
                "success": False,
                "status_code": 401,
                "detail": "API-ключ не подходит или не даёт доступа к кампаниям",
            }

        # 3. Check server readiness
        is_ready, _ = settings.is_real_mode_ready()
        if not is_ready:
            return {
                "success": False,
                "status_code": 503,
                "detail": "Не удалось проверить подключение. Попробуйте позже.",
            }

        configured_url = settings.get_real_keitaro_base_url()

        # Keitaro authorizes the submitted key against the fixed server-side tracker URL.
        base_url = normalize_tracker_url(configured_url)
        target_url = f"{base_url}/admin_api/v1/campaigns"

        try:
            if kt_client:
                resp = await kt_client.get(
                    target_url,
                    headers={"Api-Key": api_key_clean, "Accept": "application/json"},
                )
            else:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(
                        target_url,
                        headers={"Api-Key": api_key_clean, "Accept": "application/json"},
                    )

            if resp.status_code == 200:
                try:
                    campaigns = resp.json()
                except ValueError:
                    campaigns = None
                if not isinstance(campaigns, list):
                    return {
                        "success": False,
                        "status_code": 503,
                        "detail": "Не удалось проверить подключение. Попробуйте позже.",
                    }
                # Success: issue session and reset rate limit
                token = generate_session_token()
                token_h = hash_token(token)
                fp = get_key_fingerprint(api_key_clean)
                session_record = self.store.record_successful_login(
                    client_ip, token_h, fp, api_key=api_key_clean, tracker_url=base_url
                )
                csrf = generate_csrf_token(token_h)
                return {
                    "success": True,
                    "token": token,
                    "csrf_token": csrf,
                    "session": session_record,
                }
            elif resp.status_code in (401, 403):
                tier, retry_after = self.store.record_failed_attempt(client_ip)
                if retry_after > 0:
                    return {
                        "success": False,
                        "status_code": 429,
                        "detail": f"Слишком много попыток. Повторите через {retry_after} сек.",
                        "retry_after": retry_after,
                    }
                return {
                    "success": False,
                    "status_code": 401,
                    "detail": "API-ключ не подходит или не даёт доступа к кампаниям",
                }
            else:
                logger.warning(
                    "Keitaro campaigns check returned unexpected status: %d", resp.status_code
                )
                return {
                    "success": False,
                    "status_code": 503,
                    "detail": "Не удалось проверить подключение. Попробуйте позже.",
                }

        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            logger.warning(
                "Keitaro connection error during auth verification: %s", type(exc).__name__
            )
            # Network errors / timeouts do NOT increment failed password attempts
            return {
                "success": False,
                "status_code": 503,
                "detail": "Не удалось проверить подключение. Попробуйте позже.",
            }
        except Exception as exc:
            logger.error("Unexpected auth verification failure: %s", type(exc).__name__)
            return {
                "success": False,
                "status_code": 503,
                "detail": "Не удалось проверить подключение. Попробуйте позже.",
            }
