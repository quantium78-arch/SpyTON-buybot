import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from dotenv import load_dotenv

from spyton_bot.config import load_config
from spyton_bot.db import Database
from spyton_bot.detectors.pool_watcher import PoolWatcher
from spyton_bot.formatters import BuyEvent, format_channel_buy, format_group_buy
from spyton_bot.leaderboard import LeaderboardService
from spyton_bot.tonapi import TonAPI
from spyton_bot.dexscreener import DexScreener
from spyton_bot.metrics import fetch_token_metrics, MetricsCache
from spyton_bot.utils import safe_symbol

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("spyton")

async def is_owner(msg: Message, owner_id: int) -> bool:
    return bool(msg.from_user and msg.from_user.id == owner_id)

async def cmd_groupid(msg: Message):
    await msg.reply(f"Group ID: <code>{msg.chat.id}</code>", parse_mode=ParseMode.HTML)

async def cmd_status(msg: Message, db: Database):
    cfg = await db.get_group(msg.chat.id)
    await msg.reply(
        "📌 <b>Group Status</b>\n"
        f"Enabled: <b>{'ON' if cfg.enabled else 'OFF'}</b>\n"
        f"Approved (cross-post): <b>{'YES' if cfg.approved else 'NO'}</b>\n"
        f"Min buy: <b>{cfg.min_buy_ton} TON</b>\n"
        f"Token: <b>{cfg.token_symbol or '-'} </b>\n"
        f"Jetton: <code>{cfg.jetton_address or '-'}</code>\n"
        f"STONfi pool: <code>{cfg.stonfi_pool or '-'}</code>\n"
        f"DeDust pool: <code>{cfg.dedust_pool or '-'}</code>",
        parse_mode=ParseMode.HTML
    )

async def _autofill_from_jetton(group_id: int, jetton: str, db: Database, tonapi: TonAPI, ds: DexScreener):
    cache = MetricsCache(ttl_seconds=5)
    m = await fetch_token_metrics(jetton, tonapi, ds, cache)

    updates = {"jetton_address": jetton}

    sym = m.get("symbol")
    if isinstance(sym, str) and sym.strip():
        updates["token_symbol"] = safe_symbol(sym.strip())

    ston_pool = m.get("stonfi_pool")
    dedust_pool = m.get("dedust_pool")
    if isinstance(ston_pool, str) and ston_pool.strip():
        updates["stonfi_pool"] = ston_pool.strip()
    if isinstance(dedust_pool, str) and dedust_pool.strip():
        updates["dedust_pool"] = dedust_pool.strip()

    await db.set_group_fields(group_id, **updates)
    return updates

async def cmd_addtoken(msg: Message, command: CommandObject, db: Database, tonapi: TonAPI, ds: DexScreener):
    # supports:
    # /addtoken <JETTON_ADDRESS>
    # /addtoken <SYMBOL> <JETTON_ADDRESS>
    if not command.args:
        return await msg.reply("Usage: /addtoken <JETTON_ADDRESS>  OR  /addtoken <SYMBOL> <JETTON_ADDRESS>")

    parts = command.args.split()
    if len(parts) == 1:
        jetton = parts[0].strip()
        updates = await _autofill_from_jetton(msg.chat.id, jetton, db, tonapi, ds)
        await msg.reply(
            "✅ Token set (auto-detected)\n"
            f"Symbol: <b>${updates.get('token_symbol','-')}</b>\n"
            f"Jetton: <code>{updates.get('jetton_address')}</code>\n"
            f"STONfi pool: <code>{updates.get('stonfi_pool','-')}</code>\n"
            f"DeDust pool: <code>{updates.get('dedust_pool','-')}</code>",
            parse_mode=ParseMode.HTML
        )
        return

    if len(parts) >= 2:
        sym = safe_symbol(parts[0])
        jetton = parts[1].strip()
        await db.set_group_fields(msg.chat.id, token_symbol=sym, jetton_address=jetton)
        # try auto pools too
        try:
            updates = await _autofill_from_jetton(msg.chat.id, jetton, db, tonapi, ds)
        except Exception:
            updates = {"token_symbol": sym, "jetton_address": jetton}
        await msg.reply(
            f"✅ Token set: <b>${sym}</b>\nJetton: <code>{jetton}</code>\n"
            f"STONfi pool: <code>{updates.get('stonfi_pool','-')}</code>\n"
            f"DeDust pool: <code>{updates.get('dedust_pool','-')}</code>",
            parse_mode=ParseMode.HTML
        )

async def cmd_autopools(msg: Message, db: Database, tonapi: TonAPI, ds: DexScreener):
    cfg = await db.get_group(msg.chat.id)
    if not cfg.jetton_address:
        return await msg.reply("Set jetton first: /addtoken <JETTON_ADDRESS>")
    updates = await _autofill_from_jetton(msg.chat.id, cfg.jetton_address, db, tonapi, ds)
    await msg.reply(
        "✅ Auto pools refreshed\n"
        f"STONfi pool: <code>{updates.get('stonfi_pool','-')}</code>\n"
        f"DeDust pool: <code>{updates.get('dedust_pool','-')}</code>",
        parse_mode=ParseMode.HTML
    )

async def cmd_setpool(msg: Message, command: CommandObject, db: Database):
    if not command.args:
        return await msg.reply("Usage: /setpool stonfi|dedust <POOL_ADDRESS>")
    parts = command.args.split()
    if len(parts) < 2:
        return await msg.reply("Usage: /setpool stonfi|dedust <POOL_ADDRESS>")
    which = parts[0].lower()
    pool = parts[1].strip()
    if which not in ("stonfi", "dedust"):
        return await msg.reply("First arg must be stonfi or dedust")
    field = "stonfi_pool" if which == "stonfi" else "dedust_pool"
    await db.set_group_fields(msg.chat.id, **{field: pool})
    await msg.reply(f"✅ {which.upper()} pool set: <code>{pool}</code>", parse_mode=ParseMode.HTML)

async def cmd_minbuy(msg: Message, command: CommandObject, db: Database):
    if not command.args:
        return await msg.reply("Usage: /minbuy <TON>")
    try:
        v = float(command.args.strip())
    except Exception:
        return await msg.reply("Usage: /minbuy <TON> (example: /minbuy 1)")
    await db.set_group_fields(msg.chat.id, min_buy_ton=v)
    await msg.reply(f"✅ Min buy set: <b>{v} TON</b>", parse_mode=ParseMode.HTML)

async def cmd_on(msg: Message, db: Database):
    await db.set_group_fields(msg.chat.id, enabled=1)
    await msg.reply("✅ Buy tracking: <b>ON</b>", parse_mode=ParseMode.HTML)

async def cmd_off(msg: Message, db: Database):
    await db.set_group_fields(msg.chat.id, enabled=0)
    await msg.reply("🛑 Buy tracking: <b>OFF</b>", parse_mode=ParseMode.HTML)

async def cmd_approve(msg: Message, command: CommandObject, db: Database, owner_id: int):
    if not await is_owner(msg, owner_id):
        return
    if not command.args:
        return await msg.reply("Usage: /approve <GROUP_ID>")
    gid = int(command.args.strip())
    await db.set_group_fields(gid, approved=1)
    await msg.reply(f"✅ Approved group <code>{gid}</code> for channel cross-posting.", parse_mode=ParseMode.HTML)

async def cmd_revoke(msg: Message, command: CommandObject, db: Database, owner_id: int):
    if not await is_owner(msg, owner_id):
        return
    if not command.args:
        return await msg.reply("Usage: /revoke <GROUP_ID>")
    gid = int(command.args.strip())
    await db.set_group_fields(gid, approved=0)
    await msg.reply(f"🛑 Revoked group <code>{gid}</code>.", parse_mode=ParseMode.HTML)

async def cmd_pinleaderboard(msg: Message, lb: LeaderboardService, owner_id: int, bot: Bot):
    if not await is_owner(msg, owner_id):
        return
    await lb.update_once()
    if lb.message_id:
        try:
            await bot.pin_chat_message(lb.trending_channel_id, lb.message_id, disable_notification=True)
            await msg.reply(f"✅ Leaderboard posted and pinned (message_id={lb.message_id}).")
        except Exception:
            await msg.reply(f"✅ Leaderboard posted (message_id={lb.message_id}). I couldn't pin (check channel admin perms).")
    else:
        await msg.reply("Could not create leaderboard message.")

async def cmd_leaderboardnow(msg: Message, lb: LeaderboardService, owner_id: int):
    if not await is_owner(msg, owner_id):
        return
    await lb.update_once()
    await msg.reply("✅ Leaderboard updated.")

async def enrich_event(ev: BuyEvent, tonapi: TonAPI, ds: DexScreener, cache: MetricsCache, stonfi_pool: str | None, dedust_pool: str | None):
    if not ev.jetton_address:
        return ev
    m = await fetch_token_metrics(ev.jetton_address, tonapi, ds, cache)

    holders = m.get("holders")
    if isinstance(holders, (int, float)):
        ev.holders = int(holders)

    def fnum(x):
        try:
            return float(x)
        except Exception:
            return None

    ev.price_usd = fnum(m.get("price_usd")) or ev.price_usd
    ev.liquidity_usd = fnum(m.get("liquidity_usd")) or ev.liquidity_usd
    ev.mcap_usd = fnum(m.get("mcap_usd")) or ev.mcap_usd

    # links (best effort)
    ev.links = ev.links or {}
    if stonfi_pool:
        ev.links["STONfi"] = f"https://app.ston.fi/swap?pool={stonfi_pool}"
    if dedust_pool:
        ev.links["DeDust"] = f"https://app.dedust.io/swap/{ev.jetton_address}/EQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAM9c"
    if m.get("dex_url"):
        ev.links["Chart"] = m["dex_url"]
    ev.links["Trade"] = ev.links.get("STONfi") or ev.links.get("DeDust") or m.get("dex_url") or ""
    return ev

async def post_buy_to_group(bot: Bot, chat_id: int, ev: BuyEvent, book_url: str):
    text, _ = format_group_buy(ev, book_url)
    await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

async def post_buy_to_channel(bot: Bot, channel_id: int, ev: BuyEvent, book_url: str):
    text, kb = format_channel_buy(ev, book_url)
    await bot.send_message(channel_id, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=kb)

async def polling_loop(bot: Bot, db: Database, watcher: PoolWatcher, cfg, lb: LeaderboardService, tonapi: TonAPI, ds: DexScreener):
    cache = MetricsCache(ttl_seconds=15)
    while True:
        try:
            group_ids = await db.get_enabled_groups()
            group_cfgs = await asyncio.gather(*[db.get_group(gid) for gid in group_ids])

            for gcfg in group_cfgs:
                events = await watcher.poll_group(gcfg)
                for ev in events:
                    # rank key should match leaderboard key (symbol if exists else jetton)
                    key = gcfg.token_symbol or gcfg.jetton_address or "UNKNOWN"
                    ev.rank = lb.rank_map.get(key)

                    ev.jetton_address = gcfg.jetton_address
                    ev.token_symbol = safe_symbol(gcfg.token_symbol or (ev.token_symbol or "TOKEN"))
                    ev = await enrich_event(ev, tonapi, ds, cache, gcfg.stonfi_pool, gcfg.dedust_pool)

                    await post_buy_to_group(bot, gcfg.group_id, ev, cfg.book_trending_url)
                    if gcfg.approved:
                        await post_buy_to_channel(bot, cfg.trending_channel_id, ev, cfg.book_trending_url)

        except Exception as e:
            log.exception("polling error: %s", e)

        await asyncio.sleep(cfg.poll_interval_seconds)

async def main():
    load_dotenv()
    cfg = load_config()

    bot = Bot(token=cfg.bot_token, parse_mode=ParseMode.HTML)
    dp = Dispatcher()

    db = Database("spyton.db")
    await db.connect()

    tonapi = TonAPI(cfg.tonapi_base, cfg.tonapi_key)
    watcher = PoolWatcher(tonapi, db)
    ds = DexScreener(cfg.dexscreener_base)

    me = await bot.get_me()
    lb = LeaderboardService(
        bot=bot,
        db=db,
        trending_channel_id=cfg.trending_channel_id,
        updated_by=me.username or "SpyTONBot",
        window_minutes=cfg.leaderboard_window_minutes,
        interval_seconds=cfg.leaderboard_interval_seconds
    )
    if cfg.leaderboard_message_id:
        await lb.set_message_id(cfg.leaderboard_message_id)
    await lb.start()

    dp.message.register(cmd_groupid, Command("groupid"))
    dp.message.register(lambda m: cmd_status(m, db), Command("status"))
    dp.message.register(lambda m, c: cmd_addtoken(m, c, db, tonapi, ds), Command("addtoken"))
    dp.message.register(lambda m: cmd_autopools(m, db, tonapi, ds), Command("autopools"))
    dp.message.register(lambda m, c: cmd_setpool(m, c, db), Command("setpool"))
    dp.message.register(lambda m, c: cmd_minbuy(m, c, db), Command("minbuy"))
    dp.message.register(lambda m: cmd_on(m, db), Command("on"))
    dp.message.register(lambda m: cmd_off(m, db), Command("off"))

    dp.message.register(lambda m, c: cmd_approve(m, c, db, cfg.owner_id), Command("approve"))
    dp.message.register(lambda m, c: cmd_revoke(m, c, db, cfg.owner_id), Command("revoke"))
    dp.message.register(lambda m: cmd_pinleaderboard(m, lb, cfg.owner_id, bot), Command("pinleaderboard"))
    dp.message.register(lambda m: cmd_leaderboardnow(m, lb, cfg.owner_id), Command("leaderboardnow"))

    asyncio.create_task(polling_loop(bot, db, watcher, cfg, lb, tonapi, ds))
    log.info("Bot started.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
