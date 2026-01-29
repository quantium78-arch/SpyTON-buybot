import aiosqlite
from dataclasses import dataclass
from typing import Optional

SCHEMA = r'''
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS groups (
    group_id INTEGER PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    approved INTEGER NOT NULL DEFAULT 0,
    min_buy_ton REAL NOT NULL DEFAULT 0.0,
    token_symbol TEXT,
    jetton_address TEXT,
    stonfi_pool TEXT,
    dedust_pool TEXT
);

CREATE TABLE IF NOT EXISTS pool_cursors (
    pool_address TEXT PRIMARY KEY,
    last_lt INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS buys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    group_id INTEGER NOT NULL,
    dex TEXT NOT NULL,
    token_symbol TEXT,
    jetton_address TEXT,
    pool_address TEXT,
    buyer_address TEXT,
    ton_amount REAL,
    usd_amount REAL,
    jetton_amount REAL,
    tx_hash TEXT
);
'''

@dataclass
class GroupConfig:
    group_id: int
    enabled: bool
    approved: bool
    min_buy_ton: float
    token_symbol: Optional[str]
    jetton_address: Optional[str]
    stonfi_pool: Optional[str]
    dedust_pool: Optional[str]

class Database:
    def __init__(self, path: str = "spyton.db"):
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self):
        self._conn = await aiosqlite.connect(self.path)
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    @property
    def conn(self) -> aiosqlite.Connection:
        if not self._conn:
            raise RuntimeError("DB not connected")
        return self._conn

    async def ensure_group(self, group_id: int):
        await self.conn.execute("INSERT OR IGNORE INTO groups (group_id) VALUES (?)", (group_id,))
        await self.conn.commit()

    async def get_group(self, group_id: int) -> GroupConfig:
        await self.ensure_group(group_id)
        cur = await self.conn.execute(
            "SELECT group_id, enabled, approved, min_buy_ton, token_symbol, jetton_address, stonfi_pool, dedust_pool "
            "FROM groups WHERE group_id=?",
            (group_id,)
        )
        row = await cur.fetchone()
        return GroupConfig(
            group_id=row[0],
            enabled=bool(row[1]),
            approved=bool(row[2]),
            min_buy_ton=float(row[3] or 0.0),
            token_symbol=row[4],
            jetton_address=row[5],
            stonfi_pool=row[6],
            dedust_pool=row[7],
        )

    async def set_group_fields(self, group_id: int, **fields):
        await self.ensure_group(group_id)
        keys = list(fields.keys())
        vals = [fields[k] for k in keys]
        sets = ", ".join([f"{k}=?" for k in keys])
        await self.conn.execute(f"UPDATE groups SET {sets} WHERE group_id=?", (*vals, group_id))
        await self.conn.commit()

    async def set_pool_cursor(self, pool_address: str, last_lt: int):
        await self.conn.execute(
            "INSERT INTO pool_cursors (pool_address, last_lt) VALUES (?, ?) "
            "ON CONFLICT(pool_address) DO UPDATE SET last_lt=excluded.last_lt",
            (pool_address, last_lt)
        )
        await self.conn.commit()

    async def get_pool_cursor(self, pool_address: str) -> int:
        cur = await self.conn.execute("SELECT last_lt FROM pool_cursors WHERE pool_address=?", (pool_address,))
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def add_buy(self, *, ts: int, group_id: int, dex: str, token_symbol: str | None,
                      jetton_address: str | None, pool_address: str, buyer_address: str | None,
                      ton_amount: float | None, usd_amount: float | None, jetton_amount: float | None, tx_hash: str | None):
        await self.conn.execute(
            "INSERT INTO buys (ts, group_id, dex, token_symbol, jetton_address, pool_address, buyer_address, ton_amount, usd_amount, jetton_amount, tx_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, group_id, dex, token_symbol, jetton_address, pool_address, buyer_address, ton_amount, usd_amount, jetton_amount, tx_hash)
        )
        await self.conn.commit()

    async def get_recent_leaderboard(self, window_seconds: int, limit: int = 15):
        q = """
        SELECT
            COALESCE(token_symbol, jetton_address, 'UNKNOWN') AS key,
            SUM(COALESCE(usd_amount, 0)) AS vol_usd,
            COUNT(*) AS buys
        FROM buys
        WHERE ts >= strftime('%s','now') - ?
        GROUP BY key
        ORDER BY vol_usd DESC, buys DESC
        LIMIT ?
        """
        cur = await self.conn.execute(q, (window_seconds, limit))
        rows = await cur.fetchall()
        return [{"key": r[0], "vol_usd": float(r[1] or 0), "buys": int(r[2] or 0)} for r in rows]

    async def get_enabled_groups(self):
        cur = await self.conn.execute("SELECT group_id FROM groups WHERE enabled=1")
        rows = await cur.fetchall()
        return [int(r[0]) for r in rows]
