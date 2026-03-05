"""
╔══════════════════════════════════════════════════════════════════════╗
║  HYDRA MARGIN BOT - Database & State Persistence                    ║
║  SQLite with WAL mode for crash-safe atomic state management        ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import sqlite3
import json
import time
import logging
import shutil
import threading
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger("hydra.database")


class TradeState(str, Enum):
    """Trade lifecycle states."""
    PENDING = "PENDING"           # Signal generated, order not yet placed
    OPEN = "OPEN"                 # Entry filled, SL active
    TP1_HIT = "TP1_HIT"          # TP1 reached, partial close done, SL at BE
    TRAILING = "TRAILING"         # Trailing stop active on remaining position
    CLOSED = "CLOSED"             # Fully closed (profit or loss)
    CANCELLED = "CANCELLED"       # Order cancelled before fill
    ERROR = "ERROR"               # Error state requiring manual review


class TradeSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass
class TradeRecord:
    """Complete trade state record."""
    trade_id: str                          # Unique identifier: ETHUSDC_1704067200_LONG
    symbol: str
    side: str                              # LONG or SHORT
    state: str                             # TradeState value
    entry_price: float = 0.0
    entry_qty: float = 0.0                 # Total quantity entered
    remaining_qty: float = 0.0             # Quantity still open
    sl_price: float = 0.0                  # Current stop loss price
    tp1_price: float = 0.0
    tp2_price: float = 0.0
    trailing_stop: float = 0.0            # Current trailing stop level
    trailing_high: float = 0.0            # Highest price since TP1 (for longs)
    atr_at_entry: float = 0.0             # ATR when trade was opened
    entry_order_id: Optional[str] = None
    sl_order_id: Optional[str] = None
    tp1_order_id: Optional[str] = None
    pnl_realized: float = 0.0             # Realized P&L so far
    pnl_fees: float = 0.0                 # Total fees paid
    macro_bias: str = ""                   # BULLISH/BEARISH at entry
    entry_reason: str = ""                 # Signal description
    opened_at: float = 0.0                # Unix timestamp
    closed_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "TradeRecord":
        return cls(**dict(row))


class HydraDatabase:
    """
    Crash-safe state persistence using SQLite WAL mode.
    
    Design principles:
    - WAL mode: Readers never block writers, crash recovery is automatic.
    - Atomic transactions: Every state change is a single transaction.
    - Idempotent writes: Re-applying the same state change is safe.
    - Full audit trail: Nothing is ever deleted, only state-transitioned.
    """

    SCHEMA_VERSION = 1

    def __init__(self, db_path: str = "hydra_state.db"):
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        self._initialize()

    def _initialize(self):
        """Create database and tables if they don't exist."""
        self._conn = sqlite3.connect(
            str(self.db_path),
            timeout=30,
            isolation_level=None,  # Autocommit mode, we manage transactions explicitly
            check_same_thread=False,  # We handle thread safety with self._lock
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=10000")
        self._conn.execute("PRAGMA foreign_keys=ON")

        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            );

            CREATE TABLE IF NOT EXISTS trades (
                trade_id        TEXT PRIMARY KEY,
                symbol          TEXT NOT NULL,
                side            TEXT NOT NULL,
                state           TEXT NOT NULL DEFAULT 'PENDING',
                entry_price     REAL DEFAULT 0.0,
                entry_qty       REAL DEFAULT 0.0,
                remaining_qty   REAL DEFAULT 0.0,
                sl_price        REAL DEFAULT 0.0,
                tp1_price       REAL DEFAULT 0.0,
                tp2_price       REAL DEFAULT 0.0,
                trailing_stop   REAL DEFAULT 0.0,
                trailing_high   REAL DEFAULT 0.0,
                atr_at_entry    REAL DEFAULT 0.0,
                entry_order_id  TEXT,
                sl_order_id     TEXT,
                tp1_order_id    TEXT,
                pnl_realized    REAL DEFAULT 0.0,
                pnl_fees        REAL DEFAULT 0.0,
                macro_bias      TEXT DEFAULT '',
                entry_reason    TEXT DEFAULT '',
                opened_at       REAL DEFAULT 0.0,
                closed_at       REAL DEFAULT 0.0,
                updated_at      REAL DEFAULT 0.0
            );

            CREATE INDEX IF NOT EXISTS idx_trades_state ON trades(state);
            CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
            CREATE INDEX IF NOT EXISTS idx_trades_opened ON trades(opened_at);

            CREATE TABLE IF NOT EXISTS bot_state (
                key     TEXT PRIMARY KEY,
                value   TEXT NOT NULL,
                updated_at REAL DEFAULT 0.0
            );

            CREATE TABLE IF NOT EXISTS daily_stats (
                date        TEXT PRIMARY KEY,
                trades      INTEGER DEFAULT 0,
                wins        INTEGER DEFAULT 0,
                losses      INTEGER DEFAULT 0,
                pnl         REAL DEFAULT 0.0,
                fees        REAL DEFAULT 0.0,
                max_equity  REAL DEFAULT 0.0,
                min_equity  REAL DEFAULT 0.0,
                consecutive_losses INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS equity_snapshots (
                timestamp   REAL PRIMARY KEY,
                equity      REAL NOT NULL,
                free_margin REAL DEFAULT 0.0,
                positions   INTEGER DEFAULT 0
            );
        """)

        # Set schema version
        existing = self._conn.execute("SELECT version FROM schema_version").fetchone()
        if not existing:
            self._conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (self.SCHEMA_VERSION,),
            )

        logger.info(f"Database initialized: {self.db_path}")

    @contextmanager
    def transaction(self):
        """Atomic transaction context manager (thread-safe)."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    # ─── Trade Operations ────────────────────────────────────────────

    def save_trade(self, trade: TradeRecord) -> None:
        """Insert or update a trade record atomically."""
        trade.updated_at = time.time()
        d = trade.to_dict()
        columns = ", ".join(d.keys())
        placeholders = ", ".join(["?"] * len(d))
        updates = ", ".join([f"{k}=excluded.{k}" for k in d.keys() if k != "trade_id"])

        with self.transaction():
            self._conn.execute(
                f"INSERT INTO trades ({columns}) VALUES ({placeholders}) "
                f"ON CONFLICT(trade_id) DO UPDATE SET {updates}",
                list(d.values()),
            )
        logger.debug(f"Trade saved: {trade.trade_id} state={trade.state}")

    def get_trade(self, trade_id: str) -> Optional[TradeRecord]:
        """Fetch a single trade by ID."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM trades WHERE trade_id = ?", (trade_id,)
            ).fetchone()
        return TradeRecord.from_row(row) if row else None

    def get_open_trades(self, symbol: Optional[str] = None) -> list[TradeRecord]:
        """Fetch all non-closed trades."""
        active_states = (
            TradeState.PENDING.value,
            TradeState.OPEN.value,
            TradeState.TP1_HIT.value,
            TradeState.TRAILING.value,
        )
        placeholders = ",".join(["?"] * len(active_states))

        with self._lock:
            if symbol:
                rows = self._conn.execute(
                    f"SELECT * FROM trades WHERE state IN ({placeholders}) AND symbol = ? "
                    f"ORDER BY opened_at DESC",
                    (*active_states, symbol),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    f"SELECT * FROM trades WHERE state IN ({placeholders}) "
                    f"ORDER BY opened_at DESC",
                    active_states,
                ).fetchall()
        return [TradeRecord.from_row(r) for r in rows]

    def get_recent_trades(self, limit: int = 50) -> list[TradeRecord]:
        """Fetch most recent trades regardless of state."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM trades ORDER BY opened_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [TradeRecord.from_row(r) for r in rows]

    def count_trades_today(self, symbol: str) -> int:
        """Count trades opened today."""
        today_start = int(time.time()) - (int(time.time()) % 86400)
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM trades WHERE symbol = ? AND opened_at >= ?",
                (symbol, today_start),
            ).fetchone()
        return row["cnt"] if row else 0

    def get_consecutive_losses(self, symbol: str) -> int:
        """Count consecutive losing trades (most recent first)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT pnl_realized FROM trades WHERE symbol = ? AND state = ? "
                "ORDER BY closed_at DESC LIMIT 20",
                (symbol, TradeState.CLOSED.value),
        ).fetchall()
        count = 0
        for row in rows:
            if row["pnl_realized"] < 0:
                count += 1
            else:
                break
        return count

    # ─── Bot State (Key-Value) ───────────────────────────────────────

    def set_state(self, key: str, value) -> None:
        """Store arbitrary bot state."""
        with self.transaction():
            self._conn.execute(
                "INSERT INTO bot_state (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, json.dumps(value), time.time()),
            )

    def get_state(self, key: str, default=None):
        """Retrieve bot state."""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM bot_state WHERE key = ?", (key,)
            ).fetchone()
        if row:
            return json.loads(row["value"])
        return default

    # ─── Daily Stats ─────────────────────────────────────────────────

    def update_daily_stats(self, date_str: str, pnl: float, is_win: bool, fees: float = 0.0):
        """Update daily trading statistics."""
        with self.transaction():
            existing = self._conn.execute(
                "SELECT * FROM daily_stats WHERE date = ?", (date_str,)
            ).fetchone()

            if existing:
                self._conn.execute(
                    "UPDATE daily_stats SET trades = trades + 1, "
                    "wins = wins + ?, losses = losses + ?, "
                    "pnl = pnl + ?, fees = fees + ? WHERE date = ?",
                    (1 if is_win else 0, 0 if is_win else 1, pnl, fees, date_str),
                )
            else:
                self._conn.execute(
                    "INSERT INTO daily_stats (date, trades, wins, losses, pnl, fees) "
                    "VALUES (?, 1, ?, ?, ?, ?)",
                    (date_str, 1 if is_win else 0, 0 if is_win else 1, pnl, fees),
                )

    def get_daily_pnl(self, date_str: str) -> float:
        """Get total P&L for a specific day."""
        with self._lock:
            row = self._conn.execute(
                "SELECT pnl FROM daily_stats WHERE date = ?", (date_str,)
            ).fetchone()
        return row["pnl"] if row else 0.0

    # ─── Equity Tracking ─────────────────────────────────────────────

    def snapshot_equity(self, equity: float, free_margin: float, positions: int):
        """Record equity snapshot for drawdown tracking."""
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO equity_snapshots (timestamp, equity, free_margin, positions) "
                "VALUES (?, ?, ?, ?)",
                (time.time(), equity, free_margin, positions),
            )

    def get_peak_equity(self) -> float:
        """Get all-time high equity."""
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(equity) as peak FROM equity_snapshots"
            ).fetchone()
        return row["peak"] if row and row["peak"] else 0.0

    # ─── Maintenance ─────────────────────────────────────────────────

    def backup(self, suffix: str = ""):
        """Create a backup of the database."""
        backup_name = f"{self.db_path.stem}_backup{suffix}{self.db_path.suffix}"
        backup_path = self.db_path.parent / backup_name
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                shutil.copy2(str(self.db_path), str(backup_path))
            finally:
                self._conn.execute("COMMIT")
        logger.info(f"Database backed up to {backup_path}")

    def close(self):
        """Gracefully close database connection."""
        if self._conn:
            with self._lock:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._conn.close()
            self._conn = None
            logger.info("Database connection closed")