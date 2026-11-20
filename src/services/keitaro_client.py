from typing import Any, Protocol

import httpx

from src.core.config import settings
from src.core.logging import logger


class KeitaroApiError(Exception):
    """Base exception for Keitaro Admin API communication errors."""

    def __init__(self, message: str, status_code: int | None = None, response_body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class KeitaroNotFoundError(KeitaroApiError):
    """Target resource not found in Keitaro (404)."""

    pass


class KeitaroAuthError(KeitaroApiError):
    """Authentication failed or invalid API key (401/403)."""

    pass


class KeitaroConflictError(KeitaroApiError):
    """Conflict with existing resource or validation error (409/422)."""

    pass


class KeitaroGateway(Protocol):
    """Interface / Protocol for Keitaro Admin API operations."""

    async def get_domains(self) -> list[dict[str, Any]]: ...
    async def get_groups(self) -> list[dict[str, Any]]: ...
    async def get_traffic_sources(self) -> list[dict[str, Any]]: ...
    async def get_offers(self) -> list[dict[str, Any]]: ...
    async def get_offer(self, offer_id: int) -> dict[str, Any]: ...
    async def get_campaigns(self) -> list[dict[str, Any]]: ...
    async def get_campaign(self, campaign_id: int) -> dict[str, Any]: ...
    async def create_campaign(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    async def get_campaign_streams(self, campaign_id: int) -> list[dict[str, Any]]: ...
    async def create_stream(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    async def get_stream(self, stream_id: int) -> dict[str, Any]: ...
    async def update_stream(self, stream_id: int, payload: dict[str, Any]) -> dict[str, Any]: ...
    async def build_report(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class KeitaroClient:
    """Production implementation of KeitaroGateway using httpx."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 12.0,
    ):
        self.base_url = (base_url or settings.normalized_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.effective_api_key
        self.api_root = f"{self.base_url}/admin_api/v1"
        self.timeout = timeout

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Api-Key"] = self.api_key
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.api_root}/{path.lstrip('/')}"
        masked_headers = {
            k: ("***" if k.lower() == "api-key" else v) for k, v in self._get_headers().items()
        }
        logger.debug("KT request: %s %s params=%s headers=%s", method, url, params, masked_headers)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=self._get_headers(),
                    params=params,
                    json=json_data,
                )
        except httpx.TimeoutException as exc:
            logger.error("KT request timeout for %s %s: %s", method, url, exc)
            raise KeitaroApiError(
                f"Keitaro API request timed out ({self.timeout}s)", status_code=504
            ) from exc
        except httpx.RequestError as exc:
            logger.error("KT request failed for %s %s: %s", method, url, exc)
            raise KeitaroApiError(f"Network error connecting to Keitaro: {exc}") from exc

        response_text = response.text
        if self.api_key:
            response_text = response_text.replace(self.api_key, "***MASKED_KEY***")

        if response.status_code == 401 or response.status_code == 403:
            raise KeitaroAuthError(
                "Invalid Keitaro API Key or access denied", status_code=response.status_code
            )

        if response.status_code == 404:
            raise KeitaroNotFoundError(f"Resource not found at {path}", status_code=404)

        if response.status_code in (409, 422):
            raise KeitaroConflictError(
                f"Keitaro rejected request with status {response.status_code}: {response_text}",
                status_code=response.status_code,
                response_body=response_text,
            )

        if response.is_error:
            logger.error(
                "KT returned error %s for %s: %s", response.status_code, url, response_text
            )
            raise KeitaroApiError(
                f"Keitaro returned status {response.status_code}: {response_text}",
                status_code=response.status_code,
                response_body=response_text,
            )

        try:
            return response.json()
        except Exception:
            return response.text

    async def get_domains(self) -> list[dict[str, Any]]:
        result = await self._request("GET", "/domains")
        return result if isinstance(result, list) else []

    async def get_groups(self) -> list[dict[str, Any]]:
        # Query type=campaigns is required by Keitaro API
        result = await self._request("GET", "/groups", params={"type": "campaigns"})
        return result if isinstance(result, list) else []

    async def get_traffic_sources(self) -> list[dict[str, Any]]:
        result = await self._request("GET", "/traffic_sources")
        return result if isinstance(result, list) else []

    async def get_offers(self) -> list[dict[str, Any]]:
        result = await self._request("GET", "/offers")
        return result if isinstance(result, list) else []

    async def get_offer(self, offer_id: int) -> dict[str, Any]:
        return await self._request("GET", f"/offers/{offer_id}")

    async def get_campaigns(self) -> list[dict[str, Any]]:
        result = await self._request("GET", "/campaigns")
        return result if isinstance(result, list) else []

    async def get_campaign(self, campaign_id: int) -> dict[str, Any]:
        return await self._request("GET", f"/campaigns/{campaign_id}")

    async def create_campaign(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/campaigns", json_data=payload)

    async def get_campaign_streams(self, campaign_id: int) -> list[dict[str, Any]]:
        result = await self._request("GET", f"/campaigns/{campaign_id}/streams")
        return result if isinstance(result, list) else []

    async def create_stream(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/streams", json_data=payload)

    async def get_stream(self, stream_id: int) -> dict[str, Any]:
        return await self._request("GET", f"/streams/{stream_id}")

    async def update_stream(self, stream_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PUT", f"/streams/{stream_id}", json_data=payload)

    async def build_report(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = await self._request("POST", "/report/build", json_data=payload)
        return result if isinstance(result, dict) else {"rows": [], "total": 0}
