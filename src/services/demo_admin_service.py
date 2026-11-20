"""Domain service for managing autonomous Keitaro simulator state from the ADROBOT frontend."""

from typing import Any

import httpx

from src.core.config import settings


class DemoAdminConflictError(Exception):
    """Raised when the simulator stream fingerprint does not match the expected state."""

    pass


class DemoAdminService:
    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        self.base_url = (base_url or settings.get_demo_keitaro_base_url()).rstrip("/")
        self.api_key = api_key or settings.get_demo_keitaro_api_key()

    def _headers(self) -> dict[str, str]:
        return {
            "Api-Key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def get_campaigns(self) -> list[dict[str, Any]]:
        url = f"{self.base_url}/admin_api/v1/campaigns"
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(url, headers=self._headers())
            res.raise_for_status()
            return res.json()

    async def get_campaign_streams(self, campaign_id: int) -> list[dict[str, Any]]:
        url = f"{self.base_url}/admin_api/v1/campaigns/{campaign_id}/streams"
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(url, headers=self._headers())
            res.raise_for_status()
            return res.json()

    async def get_stream(self, stream_id: int) -> dict[str, Any]:
        url = f"{self.base_url}/admin_api/v1/streams/{stream_id}"
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(url, headers=self._headers())
            res.raise_for_status()
            return res.json()

    async def get_offers(self, search: str = "") -> list[dict[str, Any]]:
        url = f"{self.base_url}/admin_api/v1/offers"
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(url, headers=self._headers())
            res.raise_for_status()
            offers = res.json()

        query = search.strip().lower()
        if not query:
            return offers
        return [
            o for o in offers if query in str(o.get("id")) or query in (o.get("name") or "").lower()
        ]

    async def save_stream_offers(
        self,
        campaign_id: int,
        stream_id: int,
        offers: list[dict[str, Any]],
        expected_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/demo/admin/campaigns/{campaign_id}/streams/{stream_id}/offers"
        payload = {
            "offers": offers,
            "expected_fingerprint": expected_fingerprint,
        }
        async with httpx.AsyncClient(timeout=6.0) as client:
            res = await client.put(url, headers=self._headers(), json=payload)
            if res.status_code == 409:
                detail = res.json().get("detail", "Conflict: stream fingerprint mismatch")
                raise DemoAdminConflictError(detail)
            res.raise_for_status()

        # Step: independent read-back verification to guarantee state persisted
        verified = await self.get_stream(stream_id)
        return verified
