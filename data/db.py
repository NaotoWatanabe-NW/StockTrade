"""
SQLite 接続とスキーマ初期化

銘柄（保有/監視）と取引記録（約定履歴）を1ファイルのDBで管理する。
依存を増やさないため標準ライブラリ sqlite3 を直接使う。

テーブル:
  holdings … 現在の保有・監視銘柄（建値・株数・長期保有フラグ）
  trades   … 約定履歴（いくらで何株、売買どちらを約定したか）
"""

import os
import sqlite3
from pathlib import Path

# DBファイルの場所。環境変数 STOCK_DB_PATH で上書き可能
DEFAULT_DB_PATH = str(Path(__file__).resolve().parent.parent / "stock.db")


def db_path() -> str:
    return os.environ.get("STOCK_DB_PATH", DEFAULT_DB_PATH)


SCHEMA = """
CREATE TABLE IF NOT EXISTS holdings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT    NOT NULL UNIQUE,
    name       TEXT,
    avg_price  REAL,
    shares     REAL,
    market     TEXT,                          -- "JP"/"US"/NULL(自動判定)
    long_term  INTEGER NOT NULL DEFAULT 0,     -- 1=長期保有（売り通知を抑制し買いのみ）
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trades (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT    NOT NULL,
    name       TEXT,
    side       TEXT    NOT NULL,               -- "BUY"/"SELL"
    shares     REAL    NOT NULL,
    price      REAL    NOT NULL,               -- 約定単価
    fee        REAL    NOT NULL DEFAULT 0,     -- 手数料
    traded_at  TEXT    NOT NULL,               -- 約定日 YYYY-MM-DD
    note       TEXT,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_trades_code ON trades(code);
"""


def get_connection(path: str | None = None) -> sqlite3.Connection:
    """DB接続を返す。row_factory で dict 風アクセスを可能にし、スキーマを保証する。"""
    conn = sqlite3.connect(path or db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
