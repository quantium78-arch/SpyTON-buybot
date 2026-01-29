from __future__ import annotations
import time
from typing import Optional

from .tonapi import TonAPI
from .dexscreener import DexScreener

class MetricsCache:
    def __init__(self, ttl_seconds: int = 20):
        self.ttl = ttl_seconds
        self._cache: dict[str, tuple[float, dict]] = {}

    def get(self, key: str) -> Optional[dict]:
        item = self._cache.get(key)
        if not item:
            return None
        ts, val = item
        if time.time() - ts > self.ttl:
            return None
        return val

    def set(self, key: str, val: dict):
        self._cache[key] = (time.time(), val)

async def fetch_token_metrics(jetton_address: str, tonapi: TonAPI, ds: DexScreener, cache: MetricsCache) -> dict:
    key = jetton_address
    cached = cache.get(key)
    if cached:
        return cached

    out: dict = {}

    # TonAPI jetton info (holders + metadata)
    try:
        j = await tonapi.get_jetton(jetton_address)
        out["holders"] = j.get("holders_count") or j.get("holders") or j.get("holdersCount")
        meta = j.get("metadata") or {}
        out["symbol"] = meta.get("symbol") or j.get("symbol")
        out["decimals"] = meta.get("decimals") or j.get("decimals")
    except Exception:
        pass

    # DexScreener pairs (best for liquidity/mcap/price + pool addresses)
    data = None
    try:
        data = await ds.get_token_pairs("ton", jetton_address)
    except Exception:
        data = None

    if not data:
        try:
            data = await ds.get_token_latest(jetton_address)
        except Exception:
            data = None

    try:
        pair = DexScreener.extract_best_pair(data)
        if pair:
            out["price_usd"] = pair.get("priceUsd")
            liq = pair.get("liquidity") or {}
            out["liquidity_usd"] = liq.get("usd")
            out["mcap_usd"] = pair.get("marketCap") or pair.get("fdv")
            out["dex_url"] = pair.get("url")
            # pool address for that pair
            out["best_pair_address"] = pair.get("pairAddress") or pair.get("pair")
        # find stonfi/dedust pools if present
        pools = DexScreener.find_pools_for_dexes(data)
        out.update({f"{k}_pool": v for k, v in pools.items()})
    except Exception:
        pass

    cache.set(key, out)
    return out
