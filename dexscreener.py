from __future__ import annotations
import aiohttp
from typing import Any

class DexScreener:
    def __init__(self, base: str = "https://api.dexscreener.com"):
        self.base = base.rstrip("/")

    async def get_token_latest(self, token_address: str) -> Any:
        # Older endpoint used by many examples
        url = f"{self.base}/latest/dex/tokens/{token_address}"
        async with aiohttp.ClientSession(headers={"accept":"application/json"}) as s:
            async with s.get(url, timeout=20) as r:
                if r.status != 200:
                    return None
                return await r.json()

    async def get_token_pairs(self, chain_id: str, token_address: str) -> Any:
        # Official endpoint in DexScreener API docs:
        # /token-pairs/v1/{chainId}/{tokenAddress}
        url = f"{self.base}/token-pairs/v1/{chain_id}/{token_address}"
        async with aiohttp.ClientSession(headers={"accept":"application/json"}) as s:
            async with s.get(url, timeout=20) as r:
                if r.status != 200:
                    return None
                return await r.json()

    @staticmethod
    def extract_best_pair(data: Any) -> dict | None:
        if not data:
            return None
        # token-pairs/v1 returns list; latest/dex/tokens returns dict {pairs:[]}
        if isinstance(data, list):
            pairs = data
        elif isinstance(data, dict):
            pairs = data.get("pairs") or []
        else:
            pairs = []
        if not pairs:
            return None
        pairs_sorted = sorted(pairs, key=lambda p: (p.get("liquidity", {}) or {}).get("usd") or 0, reverse=True)
        return pairs_sorted[0] if pairs_sorted else None

    @staticmethod
    def find_pools_for_dexes(pairs_data: Any) -> dict[str, str]:
        """
        Returns { 'stonfi': pairAddress, 'dedust': pairAddress } if found.
        We match by dexId/name best-effort.
        """
        pools: dict[str, str] = {}
        pairs = []
        if isinstance(pairs_data, list):
            pairs = pairs_data
        elif isinstance(pairs_data, dict):
            pairs = pairs_data.get("pairs") or []
        for p in pairs:
            dex_id = (p.get("dexId") or p.get("dex") or p.get("dex_name") or "").lower()
            if not dex_id:
                dex_id = (p.get("labels", {}).get("dex") or "").lower()
            pair_addr = p.get("pairAddress") or p.get("pair") or p.get("pair_id")
            if not isinstance(pair_addr, str) or not pair_addr:
                continue

            if "ston" in dex_id:
                pools.setdefault("stonfi", pair_addr)
            if "dedust" in dex_id or "de_dust" in dex_id or "de dust" in dex_id:
                pools.setdefault("dedust", pair_addr)

        return pools
