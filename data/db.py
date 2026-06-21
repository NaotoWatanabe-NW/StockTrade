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
CREATE TABLE IF NOT EXISTS price_history (
    code      TEXT NOT NULL,
    interval  TEXT NOT NULL,         -- "1d" / "1wk"
    date      TEXT NOT NULL,         -- YYYY-MM-DD
    open      REAL,
    high      REAL,
    low       REAL,
    close     REAL,
    volume    REAL,
    PRIMARY KEY (code, interval, date)
);
CREATE INDEX IF NOT EXISTS idx_price_code ON price_history(code, interval, date);

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
    signal_id  INTEGER,                        -- 紐付くシグナル（NULL=シグナル外の取引）
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_trades_code ON trades(code);

-- スクリーナーが出したシグナル（注文プラン）の記録。
-- 実取引の結果をフィードバックし、ライブ成績をバックテスト期待値と比較するための土台。
--   status: OPEN(発生)→TAKEN(約定済)→CLOSED(決済完了) / SKIPPED(見送り) / EXPIRED(期限切れ)
CREATE TABLE IF NOT EXISTS signals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    code          TEXT    NOT NULL,
    name          TEXT,
    market        TEXT,                        -- "JP"/"US"
    side          TEXT    NOT NULL,            -- "BUY"/"SELL"
    signal_types  TEXT,                        -- JSON配列（["BREAKOUT_HIGH",...]）
    score         REAL,                        -- 合議スコア
    entry_price   REAL,                        -- 計画指値
    stop_price    REAL,                        -- 計画損切り
    target_price  REAL,                        -- 計画利確
    risk          REAL,                        -- 1R = entry - stop（1株あたり）
    entry_kind    TEXT,                        -- "LIMIT"/"STOP"
    order_type    TEXT,                        -- "IFDOCO" など
    status        TEXT    NOT NULL DEFAULT 'OPEN',
    realized_r    REAL,                        -- 決済完了後に計算した実現R
    notes         TEXT
);

CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_code   ON signals(code);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at          TEXT    NOT NULL DEFAULT (datetime('now')),  -- 実行日時 ISO
    universe        TEXT    NOT NULL,           -- "JP" / "US" / "ALL"
    n_signals       INTEGER,
    n_filled        INTEGER,
    fill_rate       REAL,
    n_closed        INTEGER,
    win_rate        REAL,
    avg_r           REAL,
    profit_factor   REAL,
    max_drawdown_r  REAL,
    time_stop_rate  REAL,
    params          TEXT,                       -- JSON: 使用した主要パラメータ
    sharpe          REAL,                       -- Phase 7: シャープレシオ（年率換算）
    annual_return_pct REAL,                     -- Phase 7: 年率リターン%（複利）
    equity_curve    TEXT                        -- Phase 7: 資産曲線 JSON
);
"""

# 既存 DB への後方互換マイグレーション（列が無ければ追加）
_MIGRATIONS = [
    "ALTER TABLE backtest_runs ADD COLUMN sharpe REAL",
    "ALTER TABLE backtest_runs ADD COLUMN annual_return_pct REAL",
    "ALTER TABLE backtest_runs ADD COLUMN equity_curve TEXT",
    "ALTER TABLE trades ADD COLUMN signal_id INTEGER",
]


def get_connection(path: str | None = None) -> sqlite3.Connection:
    """DB接続を返す。row_factory で dict 風アクセスを可能にし、スキーマを保証する。"""
    conn = sqlite3.connect(path or db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # 既存 DB に新列がなければ追加（ALTER TABLE はべき等でないため try/except で無視）
    for sql in _MIGRATIONS:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # 既に列が存在する場合
    conn.commit()
