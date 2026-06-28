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
    notes         TEXT,
    discord_message_id TEXT                    -- 個別通知のDiscordメッセージID（リアクション双方向用）
);

CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_code   ON signals(code);

-- シグナルの「予測（計画 entry/stop/target）」に対する実勢価格の結果。
-- 実際に取引したかに依存せず、シグナル自体の的中度を測りキャリブレーション
-- （score → 勝率/期待R）に使う。1シグナル＝1行（signal_id で一意）。
--   outcome: NO_ENTRY(指値に届かず) / TARGET(利確到達) / STOP(損切到達)
--            / TIMEOUT(期間内に未決着) / PENDING(評価期間が未経過)
CREATE TABLE IF NOT EXISTS signal_outcomes (
    signal_id        INTEGER PRIMARY KEY,          -- signals.id（1:1）
    horizon_days     INTEGER NOT NULL,             -- 評価した営業日数（保有上限）
    entry_filled     INTEGER,                      -- 1=計画 entry に価格が到達した
    entry_fill_date  TEXT,                         -- 約定（到達）日 YYYY-MM-DD
    outcome          TEXT,                          -- NO_ENTRY/TARGET/STOP/TIMEOUT/PENDING
    hit_target       INTEGER,                      -- 1=target 到達
    hit_stop         INTEGER,                      -- 1=stop 到達
    days_to_resolve  INTEGER,                      -- 約定から決着までの営業日数
    mfe_r            REAL,                          -- 最大含み益（R単位）
    mae_r            REAL,                          -- 最大含み損（R単位）
    close_at_horizon REAL,                          -- 期間末の終値
    realized_r       REAL,                          -- 予測どおりに執行した場合の実現R
    eval_through     TEXT,                          -- 評価に使った最終価格日 YYYY-MM-DD
    evaluated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (signal_id) REFERENCES signals(id) ON DELETE CASCADE
);

-- 調整可能パラメータの永続上書き（Web編集 → 次回スキャン/バックテストに実行時マージ）。
-- key='param_overrides' の1行に {param: value} のフラットJSONを保存する。
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT    PRIMARY KEY,
    value      TEXT    NOT NULL,            -- JSON
    updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS watchlist (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT    NOT NULL UNIQUE,
    name       TEXT,
    market     TEXT,                          -- "JP"/"US"/NULL(自動判定)
    note       TEXT,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_watchlist_code ON watchlist(code);

-- 銘柄→業種の分類（コード一意）。J-Quants 無料版(JP)/yfinance(US) から取得して
-- キャッシュする。sector_group は実際に合議スコアのグルーピングに使う粗い業種名
-- （JP=17業種名 / US=yfinance sector）。分類はほぼ静的なので遅延の影響を受けない。
CREATE TABLE IF NOT EXISTS sectors (
    code           TEXT PRIMARY KEY,
    name           TEXT,
    sector17_code  TEXT,
    sector17_name  TEXT,
    sector33_code  TEXT,
    sector33_name  TEXT,
    sector_group   TEXT,                       -- スコアのグルーピングに使う業種名
    market_code    TEXT,                       -- "JP"/"US"
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sectors_group ON sectors(sector_group);

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
    equity_curve    TEXT,                       -- Phase 7: 資産曲線 JSON
    status          TEXT NOT NULL DEFAULT 'done', -- Web実行ジョブの状態 running/done/error
    error           TEXT                        -- status='error' のときの失敗内容
);
"""

# 既存 DB への後方互換マイグレーション（列が無ければ追加）
_MIGRATIONS = [
    "ALTER TABLE backtest_runs ADD COLUMN sharpe REAL",
    "ALTER TABLE backtest_runs ADD COLUMN annual_return_pct REAL",
    "ALTER TABLE backtest_runs ADD COLUMN equity_curve TEXT",
    "ALTER TABLE trades ADD COLUMN signal_id INTEGER",
    "ALTER TABLE backtest_runs ADD COLUMN status TEXT NOT NULL DEFAULT 'done'",
    "ALTER TABLE backtest_runs ADD COLUMN error TEXT",
    "ALTER TABLE signals ADD COLUMN discord_message_id TEXT",
]


def get_connection(path: str | None = None) -> sqlite3.Connection:
    """DB接続を返す。row_factory で dict 風アクセスを可能にし、スキーマを保証する。

    check_same_thread=False:
        FastAPI は同期依存（api.deps.get_db）をスレッドプールで実行し、接続の
        生成（yield 前）と close（finally）が別スレッドになることがある。
        SQLite は既定で「生成スレッド以外からの使用」を禁止するため、そのままだと
        間欠的に ProgrammingError になる。接続はリクエストごとに専用で、複数スレッド
        から同時利用はされない（順次アクセスのみ）ため無効化して問題ない。
    """
    conn = sqlite3.connect(path or db_path(), check_same_thread=False)
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
