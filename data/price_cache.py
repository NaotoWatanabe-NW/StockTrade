"""
株価履歴のローカルDBキャッシュ

stock.db の price_history テーブルを一次キャッシュとして使い、
DBに無い期間だけ yfinance から取得して upsert する。

バックテスト・ライブ双方がこの経由で取得することで
  - yfinance のレート制限を回避
  - 複数回の実行でも再取得不要
  - バックテスト用の ~5年データを日足・週足で保持

使い方:
    conn = get_connection()
    df = get_history_cached(conn, "7203", JP, interval="1d", years=5)
    conn.close()
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Optional

import pandas as pd
import yfinance as yf

from core.market import Market
from data.db import get_connection

log = logging.getLogger(__name__)

_RATE_LIMIT_SEC = 1.0
_last_call: float = 0.0


def _throttle() -> None:
    global _last_call
    elapsed = time.time() - _last_call
    if elapsed < _RATE_LIMIT_SEC:
        time.sleep(_RATE_LIMIT_SEC - elapsed)
    _last_call = time.time()


def _ticker(code: str, market: Market) -> str:
    return market.ticker(code)


def _fetch_yfinance(code: str, market: Market, interval: str, years: int) -> Optional[pd.DataFrame]:
    """yfinance から取得して OHLCV の DataFrame を返す。失敗時は None。"""
    _throttle()
    period = f"{years}y"
    try:
        tk = yf.Ticker(_ticker(code, market))
        df = tk.history(period=period, interval=interval)
        if df.empty:
            log.warning(f"yfinance: データなし {code} {interval}")
            return None
        df.columns = [c.lower() for c in df.columns]
        return df
    except Exception as e:
        log.error(f"yfinance 取得エラー {code} {interval}: {e}")
        return None


def _upsert_rows(conn, code: str, interval: str, df: pd.DataFrame) -> None:
    """DataFrame の各行を price_history に upsert する。"""
    rows = []
    for idx, row in df.iterrows():
        d = idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10])
        rows.append((
            code, interval, str(d),
            _fv(row, "open"), _fv(row, "high"), _fv(row, "low"),
            _fv(row, "close"), _fv(row, "volume"),
        ))
    conn.executemany(
        """
        INSERT OR REPLACE INTO price_history
            (code, interval, date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()


def _fv(row, col: str) -> Optional[float]:
    """行から float 値を取り出す。NaN は None に変換。"""
    v = row.get(col)
    if v is None:
        return None
    try:
        f = float(v)
        return None if f != f else f  # NaN → None
    except (TypeError, ValueError):
        return None


def _load_from_db(conn, code: str, interval: str, since: date) -> Optional[pd.DataFrame]:
    """DB から指定日以降のデータを読み込む。"""
    rows = conn.execute(
        """
        SELECT date, open, high, low, close, volume
        FROM price_history
        WHERE code = ? AND interval = ? AND date >= ?
        ORDER BY date
        """,
        (code, interval, str(since)),
    ).fetchall()
    if not rows:
        return None
    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    return df


def _needs_refresh(conn, code: str, interval: str, since: date, today: date) -> bool:
    """DBに指定期間のデータがあるか（最古行が since 以前、最新行が today 以降）を確認する。"""
    row = conn.execute(
        """
        SELECT MIN(date) AS oldest, MAX(date) AS newest
        FROM price_history
        WHERE code = ? AND interval = ?
        """,
        (code, interval),
    ).fetchone()
    if not row or row["oldest"] is None:
        return True
    oldest = date.fromisoformat(row["oldest"])
    newest = date.fromisoformat(row["newest"])
    # 最古日が1週間以上 since より新しいならデータ不足
    if oldest > since + timedelta(days=7):
        return True
    # 最新日が今日より3営業日以上古ければ古いデータ
    if (today - newest).days > 5:
        return True
    return False


def get_history_cached(
    conn,
    code: str,
    market: Market,
    interval: str = "1d",
    years: int = 5,
) -> Optional[pd.DataFrame]:
    """
    price_history テーブルをキャッシュとして ~years 年分の OHLCV を返す。

    DBに十分なデータがあればそのまま返す。
    古い・不足している場合は yfinance で再取得して upsert した後に返す。

    戻り値: open/high/low/close/volume 列を持つ DatetimeIndex の DataFrame
            取得不能なら None
    """
    today = date.today()
    since = today - timedelta(days=int(years * 365.25))

    if _needs_refresh(conn, code, interval, since, today):
        log.info(f"キャッシュ更新: {code} {interval} ({years}y)")
        df_raw = _fetch_yfinance(code, market, interval, years)
        if df_raw is not None:
            _upsert_rows(conn, code, interval, df_raw)
    else:
        log.debug(f"キャッシュヒット: {code} {interval}")

    return _load_from_db(conn, code, interval, since)
