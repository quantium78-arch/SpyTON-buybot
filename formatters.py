from __future__ import annotations
from dataclasses import dataclass
from .utils import short_addr, tonviewer_tx_link

@dataclass
class BuyEvent:
    dex: str
    token_symbol: str | None
    jetton_address: str | None
    ton_amount: float | None
    usd_amount: float | None
    jetton_amount: float | None
    buyer_address: str | None
    holders: int | None = None
    price_usd: float | None = None
    liquidity_usd: float | None = None
    mcap_usd: float | None = None
    ton_price_usd: float | None = None
    tx_hash: str | None = None
    links: dict[str, str] | None = None
    rank: int | None = None

def _strength_count(usd_amount: float | None) -> int:
    if usd_amount is None:
        return 12
    if usd_amount < 50: return 8
    if usd_amount < 150: return 12
    if usd_amount < 400: return 18
    if usd_amount < 1000: return 24
    return 30

def _grid(dot: str, count: int, per_row: int = 12) -> str:
    rows = []
    for i in range(0, count, per_row):
        rows.append(dot * min(per_row, count - i))
    return "\n".join(rows)

def format_channel_buy(ev: BuyEvent, book_trending_url: str) -> tuple[str, dict | None]:
    sym = ev.token_symbol or "TOKEN"
    rank = f"[{ev.rank}] " if ev.rank else ""
    dots = _grid("🟢", _strength_count(ev.usd_amount))

    ton_line = "💎 "
    if ev.ton_amount is not None:
        ton_line += f"{ev.ton_amount:,.2f} TON"
        if ev.usd_amount is not None:
            ton_line += f" (${ev.usd_amount:,.2f})"
    else:
        ton_line += "TON buy"

    jet_line = "🪙 "
    jet_line += f"{ev.jetton_amount:,.2f} {sym}" if ev.jetton_amount is not None else sym

    buyer = short_addr(ev.buyer_address, 3)
    tx = tonviewer_tx_link(ev.tx_hash)
    tx_part = f"{buyer} | Txn" if not tx else f'<a href="{tx}">{buyer} | Txn</a>'

    lines = [
        "SpyTON / TON Trending",
        f"{rank}${sym} Buy!",
        "",
        dots,
        "",
        ton_line,
        jet_line,
        "",
        tx_part,
    ]
    if ev.holders is not None:
        lines.append(f"👥 Holders: {ev.holders:,}")
    if ev.liquidity_usd is not None:
        lines.append(f"💧 Liquidity: ${ev.liquidity_usd:,.0f}")
    if ev.mcap_usd is not None:
        lines.append(f"🏦 MCap: ${ev.mcap_usd:,.0f}")

    if ev.links:
        link_parts = []
        for k in ["Chart", "STONfi", "DeDust", "Trade"]:
            if k in ev.links and ev.links[k]:
                link_parts.append(f'<a href="{ev.links[k]}">{k}</a>')
        if link_parts:
            lines += ["", " | ".join(link_parts)]

    kb = {"inline_keyboard": [[{"text": "🔥 Book Trending", "url": book_trending_url}]]}
    return "\n".join(lines).strip(), kb

def format_group_buy(ev: BuyEvent, book_trending_url: str) -> tuple[str, dict | None]:
    sym = ev.token_symbol or "TOKEN"
    diamonds = _grid("🔻", _strength_count(ev.usd_amount))

    ton_line = "🔺 "
    if ev.ton_amount is not None:
        ton_line += f"{ev.ton_amount:,.2f} TON"
        if ev.usd_amount is not None:
            ton_line += f" (${ev.usd_amount:,.2f})"
    else:
        ton_line += "TON buy"

    jet_line = "💰 "
    jet_line += f"{ev.jetton_amount:,.2f} {sym}" if ev.jetton_amount is not None else sym

    buyer = short_addr(ev.buyer_address, 3)
    tx = tonviewer_tx_link(ev.tx_hash)
    tx_part = f"{buyer} | Txn" if not tx else f'<a href="{tx}">{buyer} | Txn</a>'

    lines = [
        f"{sym} Buy!",
        "",
        diamonds,
        "",
        ton_line,
        jet_line,
        "",
        tx_part,
    ]
    if ev.holders is not None:
        lines.append(f"👥 Holders: {ev.holders:,}")
    if ev.price_usd is not None:
        lines.append(f"💵 Price: ${ev.price_usd:,.8f}".rstrip("0").rstrip("."))
    if ev.liquidity_usd is not None:
        lines.append(f"💧 Liquidity: ${ev.liquidity_usd:,.0f}")
    if ev.mcap_usd is not None:
        lines.append(f"🏦 MCap: ${ev.mcap_usd:,.0f}")
    if ev.ton_price_usd is not None:
        lines.append(f"🟦 TON Price: ${ev.ton_price_usd:,.4f}")

    lines += ["──────────────", f'<a href="{book_trending_url}">You can book an ad here</a>']
    return "\n".join(lines).strip(), None

def format_leaderboard(items: list[dict], updated_by: str) -> str:
    updated_by = updated_by.lstrip("@")
    lines = [f"🔴 @{updated_by}", ""]
    def row(rank: int, key: str) -> str:
        if rank <= 3:
            block = "🟥"
        elif rank <= 10:
            block = "⬛"
        else:
            block = "🟩"
        return f"{block} {rank} - ${key}"
    for i, it in enumerate(items[:3], start=1):
        lines.append(row(i, it["key"]))
    lines.append("──────────────")
    for i, it in enumerate(items[3:10], start=4):
        lines.append(row(i, it["key"]))
    lines.append("──────────────")
    for i, it in enumerate(items[10:15], start=11):
        lines.append(row(i, it["key"]))
    lines += ["", f"ℹ️ Trending data is automatically updated by @{updated_by} every 10 seconds"]
    return "\n".join(lines).strip()
