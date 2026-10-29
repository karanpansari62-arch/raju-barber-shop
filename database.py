import sqlite3
from typing import Generator
from config import DATABASE_PATH

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

-- 1. Services provided by the barber
CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL,
    price_inr INTEGER NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);

-- 2. Single-chair bookings
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id INTEGER NOT NULL REFERENCES services(id),
    customer_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    start_at TEXT NOT NULL,            -- ISO-8601 with timezone (Asia/Kolkata)
    end_at TEXT NOT NULL,              -- ISO-8601 with timezone (Asia/Kolkata)
    status TEXT NOT NULL CHECK(status IN ('confirmed', 'cancelled', 'completed', 'no_show')),
    cancel_token_hash TEXT NOT NULL,   -- SHA-256 hex digest
    created_at TEXT NOT NULL
);

-- 3. Blocked times (emergencies / unscheduled off-times)
CREATE TABLE IF NOT EXISTS blocked_times (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    reason TEXT NOT NULL
);

-- Fast lookup indexes
CREATE INDEX IF NOT EXISTS idx_services_active ON services(is_active);
CREATE INDEX IF NOT EXISTS idx_bookings_active ON bookings(status, start_at, end_at);
CREATE INDEX IF NOT EXISTS idx_bookings_phone ON bookings(phone, status, end_at);
CREATE INDEX IF NOT EXISTS idx_bookings_token ON bookings(cancel_token_hash);
"""

def get_connection(db_path: str = None) -> sqlite3.Connection:
    """
    Returns an SQLite connection with Foreign Keys enabled, WAL journal mode,
    and isolation_level=None to support atomic BEGIN IMMEDIATE transactions.
    """
    target = db_path if db_path is not None else DATABASE_PATH
    conn = sqlite3.connect(target, timeout=10.0, check_same_thread=False, isolation_level=None)
    # Foreign keys must be explicitly enabled per connection in SQLite
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.row_factory = sqlite3.Row
    return conn

def init_db(db_path: str = None) -> None:
    """Initializes tables and indexes."""
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
    finally:
        conn.close()

def get_db() -> Generator[sqlite3.Connection, None, None]:
    """FastAPI route dependency yielding an open connection."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()
