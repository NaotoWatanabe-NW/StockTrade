"""
決算回避フィルタ（Phase 3）

エントリー有効期限（15 営業日）内に決算発表がある場合、
ギャップリスクを避けるためエントリーを見送る。

────────────────────────────────────────────────────────
制約と設計方針
  - yfinance の決算日データは欠損・誤りが多い（特に日本株）。
  - データが取れない場合は「見送らない」（False 回避）を既定とする。
  - バックテストでは過去時点の決算日が取れないため、このフィルタは
    ライブスクリーニングでのみ有効。simulator.py は apply しない。
────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

log = logging.getLogger(__name__)


def get_next_earnings_date(code: str, market_code: str) -> Optional[date]:
    """
    yfinance から次回決算予定日を取得する。

    取得できない場合（欠損・エラー）は None を返す。
    日本株は calendar データが乏しく None になりやすい。
    """
    try:
        import yfinance as yf
        from core.market import resolve_market
        market = resolve_market(code)
        ticker_code = market.ticker(code)
        tk = yf.Ticker(ticker_code)
        cal = tk.calendar
        if cal is None or cal.empty:
            return None
        # calendar は {"Earnings Date": [...], ...} 形式の DataFrame が多い
        if "Earnings Date" in cal.index:
            val = cal.loc["Earnings Date"]
            # Series の場合は最初の要素を使う
            if hasattr(val, "iloc"):
                val = val.iloc[0]
            if val is None:
                return None
            return pd.Timestamp(val).date()
        return None
    except Exception as e:
        log.debug(f"決算日取得失敗 {code}: {e}")
        return None


def should_avoid_earnings(
    code: str,
    market_code: str,
    t_date: date,
    cfg: dict,
) -> bool:
    """
    エントリー有効期限内に決算がある場合 True（= 見送り）を返す。

    t_date  : シグナル発生日
    cfg     : EVENTS_CONFIG
              avoid_earnings_within_days : 何営業日以内の決算を回避するか
    """
    avoid_days = int(cfg.get("avoid_earnings_within_days", 15))
    earnings = get_next_earnings_date(code, market_code)
    if earnings is None:
        return False  # データ無し → 見送らない
    # 決算日がエントリー有効期限内かどうか（カレンダー日で近似）
    deadline = t_date + timedelta(days=avoid_days * 1.5)  # 営業日→カレンダー日換算の余裕
    return t_date <= earnings <= deadline


# pandas は get_next_earnings_date 内のみ使用するため遅延インポート
try:
    import pandas as pd
except ImportError:
    pass
