from __future__ import annotations
import asyncio
import base64
import logging
import re

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from dotenv import load_dotenv

from config import load_config, Config
from db import Database
from pool_watcher import PoolWatcher
from formatters import BuyEvent, format_channel_buy, format_group_buy
from leaderboard import LeaderboardService
from tonapi import TonAPI
from dexscreener import DexScreener
from metrics import fetch_token_metrics, MetricsCache
from utils import safe_symbol


# --- Channel de-duplication (avoid posting same TX multiple times if many groups track same token) ---
CHANNEL_DEDUPE_TTL = 120  # seconds
_channel_recent: dict[str, float] = {}

def _channel_seen(key: str, now: float) -> bool:
    # cleanup
    expired = [k for k,v in _channel_recent.items() if now - v > CHANNEL_DEDUPE_TTL]
    for k in expired:
        _channel_recent.pop(k, None)
    if key in _channel_recent:
        return True
    _channel_recent[key] = now
    return False

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("spyton")


def parse_chat_id(raw: str) -> int:
    # Accept normal hyphen '-', and also en/em dashes from iOS keyboards
    s = (raw or "").strip()
    s = s.replace("–", "-").replace("—", "-").replace("−", "-")
    # keep only leading '-' and digits
    m = re.search(r"-?\d+", s)
    if not m:
        raise ValueError("invalid chat id")
    return int(m.group(0))

async def is_owner(msg: Message, owner_id: int) -> bool:
    return bool(msg.from_user and msg.from_user.id == owner_id)


async def cmd_start(msg: Message, command: CommandObject, cfg: Config, db: Database):
    # Private chat only: Suite-style UI (no commands needed)
    if msg.chat.type != "private":
        return

    arg = (command.args or "").strip()

    # deep-link from group: /start cfg_<payload>
    if arg.startswith("cfg_"):
        payload = arg[4:]
        try:
            gid = int(base64.urlsafe_b64decode(payload + "===").decode())
        except Exception:
            gid = None

        if gid:
            _USER_FLOW[msg.from_user.id] = {"group_id": gid, "step": None}
            return await ui_show_config_menu(msg, cfg, db, gid)

    me = await msg.bot.get_me()
    username = me.username or ""
    add_group_url = f"https://t.me/{username}?startgroup=1" if username else None

    kb_rows = []
    if add_group_url:
        kb_rows.append([InlineKeyboardButton(text="➕ Add me to your Group", url=add_group_url)])
    kb_rows.append([InlineKeyboardButton(text="➕ Add new Token", callback_data="menu:add_token")])
    kb_rows.append([InlineKeyboardButton(text="⚙️ Edit BuyBot Settings", callback_data="menu:token_settings")])
    kb_rows.append([InlineKeyboardButton(text="📚 Getting Started", callback_data="menu:guide")])

    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    text = (
        "🤖 **SpyTON BuyBot**\n\n"
        "Tap the buttons below to set up (no commands).\n\n"
        "✅ Add bot to your group\n"
        "✅ Make it admin\n"
        "✅ Configure token in DM\n\n"
        "Buys will post in your group and in **{tr}**."
    ).format(tr=cfg.trending_channel_username_username)

    await msg.answer(text, parse_mode="Markdown", reply_markup=kb)(text, parse_mode="Markdown", reply_markup=kb)


async def cmd_groupid(msg: Message):
    await msg.reply(f"Group ID: <code>{msg.chat.id}</code>", parse_mode=ParseMode.HTML)

async def cmd_status(msg: Message, db: Database):
    cfg = await db.get_group(msg.chat.id)
    await msg.reply(
        "📌 <b>Group Status</b>\n"
        f"Enabled: <b>{'ON' if cfg.enabled else 'OFF'}</b>\n"        f"Min buy: <b>{cfg.min_buy_ton} TON</b>\n"
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
    # Approval is NOT required. Bot works in all groups.
    await msg.reply("✅ Approval is not required. Add the bot as Admin and use /addtoken then /on.", parse_mode=ParseMode.HTML)


async def cmd_revoke(msg: Message, command: CommandObject, db: Database, owner_id: int):
    # Revoke is disabled in this public version
    await msg.reply("ℹ️ This bot supports all groups. If you want to disable a group, use /off inside that group.", parse_mode=ParseMode.HTML)


async def cb_menu(call: CallbackQuery, cfg: Config, db: Database):
    data = (call.data or "")
    await call.answer()
    if not call.message:
        return

    async def _render_home():
        me = await call.bot.get_me()
        username = me.username or ""
        add_group_url = f"https://t.me/{username}?startgroup=1" if username else None

        kb_rows = []
        if add_group_url:
            kb_rows.append([InlineKeyboardButton(text="➕ Add me to your Group", url=add_group_url)])
        kb_rows.append([InlineKeyboardButton(text="➕ Add new Token", callback_data="menu:add_token")])
        kb_rows.append([InlineKeyboardButton(text="⚙️ Edit BuyBot Settings", callback_data="menu:token_settings")])
        kb_rows.append([InlineKeyboardButton(text="📚 Getting Started", callback_data="menu:guide")])

        kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

        text = (
            "🤖 **SpyTON BuyBot**\n\n"
            "Suite-style setup (no commands):\n"
            "1) Tap **Add BuyBot to Group**\n"
            "2) Add the bot as **Admin**\n"
            "3) In the group, tap **Configure Token**\n"
            "4) Paste your token Jetton address\n\n"
            "Buys will post in your group and also in **@{tr}**."
        ).format(tr=cfg.trending_channel_username_username.lstrip("@"))
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

    if data in ("menu:home", "menu:back"):
        return await _render_home()

    if data == "menu:guide":
        await call.message.edit_text(
            "📘 **Quick Guide**\n\n"
            "• Add the bot to your group as **Admin**\n"
            "• In the group, tap **Configure Token**\n"
            "• In private chat, paste your token **Jetton master address** (EQ… / UQ…)\n"
            "• Done ✅ Buys start posting automatically\n\n"
            "Posts go to your group **and** @{tr}.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Back", callback_data="menu:home")]
            ])
        )
        return

    # ---------------- Group picker ----------------
    if data in ("menu:add_token", "menu:token_settings"):
        groups = await db.list_groups()
        if not groups:
            await call.message.edit_text(
                "No groups connected yet.\n\nAdd the bot to a group first.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Back", callback_data="menu:home")],
                ])
            )
            return

        mode = "add_token" if data == "menu:add_token" else "settings"
        kb_rows = []
        for gid in groups[:30]:
            kb_rows.append([InlineKeyboardButton(text=f"Group {gid}", callback_data=f"pick:{mode}:{gid}")])
        kb_rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="menu:home")])
        await call.message.edit_text(
            "Choose the group you want to configure:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
        )
        return

    # ---------------- After pick group ----------------
    if data.startswith("pick:"):
        parts = data.split(":")
        if len(parts) != 3:
            return
        mode, gid_s = parts[1], parts[2]
        try:
            gid = int(gid_s)
        except ValueError:
            return

        _USER_FLOW[call.from_user.id] = {"group_id": gid, "step": None}

        if mode == "add_token":
            _USER_FLOW[call.from_user.id]["step"] = "await_jetton"
            await call.message.edit_text(
                "📌 **Send your token Jetton master address**\n\n"
                "Example: `EQ...`\n\n"
                "I will auto-detect pools (STON.fi + DeDust) and start posting buys.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Back", callback_data="menu:home")]
                ])
            )
            return

        return await ui_show_config_menu(call.message, cfg, db, gid)

    # ---------------- Settings actions ----------------
    if data == "cfg:minbuy":
        st = _USER_FLOW.get(call.from_user.id) or {}
        gid = st.get("group_id")
        if not gid:
            await call.message.edit_text("Open Token Settings from the main menu first.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back", callback_data="menu:home")]]))
            return
        _USER_FLOW[call.from_user.id]["step"] = "await_minbuy"
        await call.message.edit_text(
            "💰 **Set Min Buy (TON)**\n\nSend a number like `0.5`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back", callback_data="menu:home")]])
        )
        return

    if data == "cfg:remove":
        st = _USER_FLOW.get(call.from_user.id) or {}
        gid = st.get("group_id")
        if not gid:
            return
        await db.set_group_fields(gid, jetton=None, stonfi_pool=None, dedust_pool=None, symbol=None, decimals=None, enabled=0)
        await call.message.edit_text(
            "✅ Token removed for this group.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back", callback_data="menu:home")]])
        )
        return

    return await _render_home()


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

# ===================== UI STATE =====================
_USER_FLOW: dict[int, dict] = {}

def _enc_gid(gid: int) -> str:
    return base64.urlsafe_b64encode(str(gid).encode()).decode().rstrip("=")

# ===================== UI SCREENS =====================
async def ui_show_config_menu(msg: Message, cfg: Config, db: Database, group_id: int):
    g = await db.get_group(group_id)
    token_line = "❌ Not set"
    if g and g.jetton:
        sym = g.symbol or "TOKEN"
        token_line = f"✅ {sym} — `{g.jetton}`"

    min_buy = g.min_buy_ton if g else 0.0
    enabled = "ON ✅" if (g and g.enabled) else "OFF ⏸️"

    kb_rows = []
    if not (g and g.jetton):
        kb_rows.append([InlineKeyboardButton(text="➕ Add new Token", callback_data="menu:add_token")])
    kb_rows += [
        [InlineKeyboardButton(text=f"💰 Min Buy ({min_buy} TON)", callback_data="cfg:minbuy")],
        [InlineKeyboardButton(text="🗑️ Remove Token", callback_data="cfg:remove")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="menu:home")],
    ]

    text = (
        "⚙️ **Token Settings**\n\n"
        f"Group: `{group_id}`\n"
        f"Token: {token_line}\n"
        f"Status: {enabled}\n\n"
        "Change settings using the buttons below."
    )

    await msg.answer(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))

# ===================== GROUP CONNECT (AUTO) =====================
async def on_bot_added_to_group(msg: Message, cfg: Config, db: Database):
    if msg.chat.type not in ("group", "supergroup"):
        return
    if not msg.new_chat_members:
        return
    me = await msg.bot.get_me()
    if not any(u.id == me.id for u in msg.new_chat_members):
        return

    await db.ensure_group(msg.chat.id)
    await db.set_group_fields(msg.chat.id, enabled=0)

    deep = f"https://t.me/{me.username}?start=cfg_{_enc_gid(msg.chat.id)}" if me.username else None
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ Configure BuyBot", url=deep)] if deep else []
    ]) if deep else None

    await msg.answer(
        "✅ Your group has been connected to **SpyTON BuyBot**.\n\n"
        "Tap **Configure Token** to finish setup.",
        parse_mode="Markdown",
        reply_markup=kb
    )

# ===================== PRIVATE INPUT HANDLER =====================
async def on_private_text(msg: Message, cfg: Config, db: Database, tonapi: TonApi, ds: Dexscreener):
    if msg.chat.type != "private":
        return
    st = _USER_FLOW.get(msg.from_user.id)
    if not st:
        return

    step = st.get("step")
    gid = st.get("group_id")
    if not gid:
        return

    txt = (msg.text or "").strip()

    if step == "await_jetton":
        jetton = txt.split()[0]
        await msg.answer("⏳ Connecting token…")
        try:
            updates = await _autofill_from_jetton(gid, jetton, db, tonapi, ds)
            await db.set_group_fields(gid, **updates, enabled=1)
        except Exception:
            await msg.answer("❌ Failed to connect token. Please send a valid Jetton master address (EQ…/UQ…).")
            return
        _USER_FLOW[msg.from_user.id]["step"] = None
        await msg.answer("✅ Token connected successfully! Buys will start posting.")
        return await ui_show_config_menu(msg, cfg, db, gid)

    if step == "await_minbuy":
        try:
            val = float(txt.replace(",", "."))
            if val < 0:
                raise ValueError
        except Exception:
            await msg.answer("Send a valid number like `0.5`", parse_mode="Markdown")
            return
        await db.set_group_fields(gid, min_buy_ton=val)
        _USER_FLOW[msg.from_user.id]["step"] = None
        await msg.answer("✅ Min Buy updated.")
        return await ui_show_config_menu(msg, cfg, db, gid)




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
                     # Cross-post to trending channel for all groups, with de-dupe
                    key = ev.tx_hash or f"{ev.dex}:{ev.pool_address}:{ev.buyer_address}:{ev.ton_amount}:{ev.jetton_amount}:{int(ev.ts)}"
                    if not _channel_seen(key, asyncio.get_event_loop().time()):
                        await post_buy_to_channel(bot, (cfg.trending_channel_username_id or cfg.trending_channel_username_username), ev, cfg.book_trending_url)

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
        trending_channel_id=(cfg.trending_channel_username_id or cfg.trending_channel_username_username),
        updated_by=me.username or "SpyTONBot",
        window_minutes=cfg.leaderboard_window_minutes,
        interval_seconds=cfg.leaderboard_interval_seconds
    )
    if cfg.leaderboard_message_id:
        await lb.set_message_id(cfg.leaderboard_message_id)
    await lb.start()

    dp.message.register(lambda m, c: cmd_start(m, c, cfg, db), CommandStart())
    dp.message.register(lambda m: on_bot_added_to_group(m, cfg, db), F.new_chat_members)
    dp.message.register(lambda m: on_private_text(m, cfg, db, tonapi, ds), F.chat.type == "private")
    dp.callback_query.register(lambda c: cb_menu(c, cfg, db))

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
