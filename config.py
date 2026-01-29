import os
from dataclasses import dataclass

def _get_int(name: str, default: int) -> int:
    v = os.getenv(name, "").strip()
    return int(v) if v else default

def _get_float(name: str, default: float) -> float:
    v = os.getenv(name, "").strip()
    return float(v) if v else default

@dataclass(frozen=True)
class Config:
    bot_token: str
    owner_id: int
    trending_channel_id: int
    trending_channel_username: str
    book_trending_url: str

    tonapi_base: str
    tonapi_key: str
    dexscreener_base: str

    poll_interval_seconds: float
    leaderboard_interval_seconds: float
    leaderboard_window_minutes: int
    leaderboard_message_id: int | None

def load_config() -> Config:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("Missing BOT_TOKEN in .env")

    owner_id = _get_int("OWNER_ID", 0)
    if not owner_id:
        raise RuntimeError("Missing OWNER_ID in .env")

    trending_channel_id = _get_int("TRENDING_CHANNEL_ID", 0)
    if not trending_channel_id:
        raise RuntimeError("Missing TRENDING_CHANNEL_ID in .env")

    return Config(
        bot_token=bot_token,
        owner_id=owner_id,
        trending_channel_id=trending_channel_id,
        trending_channel_username=os.getenv("TRENDING_CHANNEL_USERNAME", "@SpyTonTrending").strip(),
        book_trending_url=os.getenv("BOOK_TRENDING_URL", "https://t.me/SpyTONTrndBot").strip(),
        tonapi_base=os.getenv("TONAPI_BASE", "https://tonapi.io").strip().rstrip("/"),
        tonapi_key=os.getenv("TONAPI_KEY", "").strip(),
        dexscreener_base=os.getenv("DEXSCREENER_BASE", "https://api.dexscreener.com").strip().rstrip("/"),
        poll_interval_seconds=_get_float("POLL_INTERVAL_SECONDS", 1.0),
        leaderboard_interval_seconds=_get_float("LEADERBOARD_INTERVAL_SECONDS", 10.0),
        leaderboard_window_minutes=_get_int("LEADERBOARD_WINDOW_MINUTES", 15),
        leaderboard_message_id=_get_int("LEADERBOARD_MESSAGE_ID", 0) or None,
    )
