from __future__ import annotations
import asyncio
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from .db import Database
from .formatters import format_leaderboard

class LeaderboardService:
    def __init__(self, bot: Bot, db: Database, trending_channel_id: int, updated_by: str, window_minutes: int, interval_seconds: float):
        self.bot = bot
        self.db = db
        self.trending_channel_id = trending_channel_id
        self.updated_by = updated_by.lstrip("@")
        self.window_seconds = window_minutes * 60
        self.interval_seconds = interval_seconds
        self.message_id: int | None = None
        self._task: asyncio.Task | None = None
        self.rank_map: dict[str, int] = {}

    async def set_message_id(self, message_id: int):
        self.message_id = message_id

    async def start(self):
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run())

    async def _run(self):
        while True:
            try:
                await self.update_once()
            except Exception:
                pass
            await asyncio.sleep(self.interval_seconds)

    async def update_once(self):
        items = await self.db.get_recent_leaderboard(self.window_seconds, limit=15)
        # refresh rank map for auto-rank in buy posts
        self.rank_map = {it["key"]: i for i, it in enumerate(items, start=1)}
        text = format_leaderboard(items, updated_by=self.updated_by)

        if not self.message_id:
            msg = await self.bot.send_message(self.trending_channel_id, text)
            self.message_id = msg.message_id
            return

        try:
            await self.bot.edit_message_text(text=text, chat_id=self.trending_channel_id, message_id=self.message_id)
        except TelegramBadRequest:
            msg = await self.bot.send_message(self.trending_channel_id, text)
            self.message_id = msg.message_id
