from __future__ import annotations
import asyncio
import time
from typing import Any, Optional

from ..tonapi import TonAPI
from ..db import Database, GroupConfig
from ..formatters import BuyEvent
from ..utils import safe_symbol, nano_to_ton, nano_to_units

def _walk(obj: Any):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(obj, list):
        for it in obj:
            yield from _walk(it)

def _find_first(obj: Any, keys: set[str]) -> Any:
    for k, v in _walk(obj):
        if k in keys:
            return v
    return None

def _find_numbers(obj: Any, keys: set[str]) -> list[float]:
    out = []
    for k, v in _walk(obj):
        if k in keys:
            try:
                if isinstance(v, (int, float)):
                    out.append(float(v))
                elif isinstance(v, str):
                    s = v.strip()
                    if s and s.replace(".", "", 1).isdigit():
                        out.append(float(s))
            except Exception:
                pass
    return out

def _extract_jetton_transfer(trace: Any, jetton_address: str | None) -> tuple[Optional[float], Optional[str]]:
    """
    Best-effort: search trace for a jetton transfer amount and recipient.
    This logic is heuristic because TonAPI trace schemas vary.
    """
    if not trace:
        return None, None

    # Common patterns: "jetton_transfer" / "JettonTransfer" / "jetton" with "amount"
    amount_raw = None
    recipient = None

    for k, v in _walk(trace):
        lk = str(k).lower()
        if "recipient" in lk or lk in ("to", "destination", "dst"):
            if isinstance(v, str) and v.startswith("UQ"):
                recipient = v
        if "jetton" in lk and isinstance(v, str) and v.startswith("EQ"):
            # could be jetton master address
            if jetton_address and v != jetton_address:
                continue

        if lk in ("jetton_amount", "jettonamount", "amount") and isinstance(v, (int, float, str)):
            amount_raw = v

    # If we found numeric candidates, pick the largest (usually actual transfer)
    nums = _find_numbers(trace, {"jetton_amount", "jettonamount", "amount"})
    num = max(nums) if nums else None
    if num is None and amount_raw is not None:
        try:
            num = float(amount_raw)
        except Exception:
            num = None

    return num, recipient

class PoolWatcher:
    def __init__(self, tonapi: TonAPI, db: Database):
        self.tonapi = tonapi
        self.db = db

    async def poll_group(self, cfg: GroupConfig) -> list[BuyEvent]:
        if not cfg.enabled:
            return []
        events: list[BuyEvent] = []
        sym = safe_symbol(cfg.token_symbol or "TOKEN")

        async def poll_pool(dex: str, pool: str) -> list[BuyEvent]:
            last_lt = await self.db.get_pool_cursor(pool)
            try:
                data = await self.tonapi.get_account_transactions(pool, limit=25)
            except Exception:
                return []

            txs = data.get("transactions") or data.get("items") or []
            new = []
            for tx in txs:
                try:
                    lt = int(tx.get("transaction_id", {}).get("lt") or tx.get("lt") or 0)
                except Exception:
                    lt = 0
                if lt and lt > last_lt:
                    new.append((lt, tx))
            if not new:
                return []

            new.sort(key=lambda x: x[0])  # oldest first
            newest_lt = last_lt
            out: list[BuyEvent] = []

            for lt, tx in new:
                newest_lt = max(newest_lt, lt)
                in_msg = tx.get("in_msg") or {}

                ton_amount = nano_to_ton(in_msg.get("value"))
                tx_hash = tx.get("transaction_id", {}).get("hash") or tx.get("hash")
                buyer = in_msg.get("source") or in_msg.get("src") or in_msg.get("from")

                # Skip tiny messages
                if cfg.min_buy_ton and ton_amount is not None and ton_amount < cfg.min_buy_ton:
                    continue

                usd_amount = None
                jetton_amount = None

                # Prefer trace parsing (more accurate)
                trace_id = tx.get("trace_id") or tx.get("traceId") or _find_first(tx, {"trace_id", "traceId"})
                if isinstance(trace_id, str) and trace_id:
                    try:
                        trace = await self.tonapi.get_trace(trace_id)
                        raw_jet, _recip = _extract_jetton_transfer(trace, cfg.jetton_address)
                        # If we later know decimals, we can convert; for now assume raw is already in units OR big nano.
                        # Heuristic: if it's huge, treat as nano with 9 decimals
                        if raw_jet is not None:
                            if raw_jet > 1e12:
                                jetton_amount = raw_jet / 1e9
                            else:
                                jetton_amount = raw_jet
                        # USD value sometimes included
                        u = _find_numbers(trace, {"value_usd", "amount_usd", "usd"})
                        usd_amount = max(u) if u else None
                    except Exception:
                        pass

                # Fallback to any embedded numbers
                if usd_amount is None:
                    nums = _find_numbers(tx, {"value_usd", "amount_usd", "usd", "total_value_usd"})
                    usd_amount = max(nums) if nums else None
                if jetton_amount is None:
                    numsj = _find_numbers(tx, {"jetton_amount", "jettonAmount"})
                    jetton_amount = max(numsj) if numsj else None

                ev = BuyEvent(
                    dex=dex,
                    token_symbol=sym,
                    jetton_address=cfg.jetton_address,
                    ton_amount=ton_amount,
                    usd_amount=usd_amount,
                    jetton_amount=jetton_amount,
                    buyer_address=buyer,
                    tx_hash=tx_hash,
                )
                out.append(ev)

                await self.db.add_buy(
                    ts=int(time.time()),
                    group_id=cfg.group_id,
                    dex=dex,
                    token_symbol=sym,
                    jetton_address=cfg.jetton_address,
                    pool_address=pool,
                    buyer_address=buyer,
                    ton_amount=ton_amount,
                    usd_amount=usd_amount,
                    jetton_amount=jetton_amount,
                    tx_hash=tx_hash
                )

            await self.db.set_pool_cursor(pool, newest_lt)
            return out

        tasks = []
        if cfg.stonfi_pool:
            tasks.append(poll_pool("STONfi", cfg.stonfi_pool))
        if cfg.dedust_pool:
            tasks.append(poll_pool("DeDust", cfg.dedust_pool))

        if not tasks:
            return []

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, list):
                events.extend(r)
        return events
