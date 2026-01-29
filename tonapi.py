from __future__ import annotations
import aiohttp
from typing import Any

class TonAPI:
    def __init__(self, base: str, api_key: str | None = None):
        self.base = base.rstrip("/")
        self.api_key = (api_key or "").strip()

    def _headers(self) -> dict[str, str]:
        h = {"accept": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        url = f"{self.base}{path}"
        async with aiohttp.ClientSession(headers=self._headers()) as s:
            async with s.get(url, params=params, timeout=20) as r:
                r.raise_for_status()
                return await r.json()

    async def get_account_transactions(self, address: str, limit: int = 20) -> Any:
        return await self._get(f"/v2/blockchain/accounts/{address}/transactions", {"limit": str(limit)})

    async def get_trace(self, trace_id: str) -> Any:
        return await self._get(f"/v2/traces/{trace_id}")

    async def get_jetton(self, jetton_address: str) -> Any:
        # Many TonAPI deployments support this path. If not, it will throw.
        return await self._get(f"/v2/jettons/{jetton_address}")

    async def get_rates(self) -> Any:
        # Try to fetch rates; endpoint varies. We'll try common ones.
        try:
            return await self._get("/v2/rates")
        except Exception:
            return None
