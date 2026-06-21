"""
株価データ取得クライアント

yfinance（Yahoo!ファイナンス）を使用。無料・登録不要で利用可能。
日本株は "コード.T"（例: 7203.T）、米国株はティッカーそのまま（例: AAPL）。
市場判定は core.market が担当し、本クライアントは Market を受け取って動く。

注意:
  - 短すぎる間隔での連続リクエストはYahoo側にブロックされる可能性あり
  - リアルタイムではなく15〜20分遅延のデータ（スイング向け）
"""

import time
import logging
import pandas as pd
import yfinance as yf
from typing import Optional

from core.market import Market, resolve_market

log = logging.getLogger(__name__)


class StockDataClient:
    """日本株・米国株データ取得クライアント"""

    def __init__(self, rate_limit_sec: float = 1.0):
        self.rate_limit_sec = rate_limit_sec
        self._last_call = 0.0

    def _throttle(self):
        """連続リクエスト時のレート制限"""
        elapsed = time.time() - self._last_call
        if elapsed < self.rate_limit_sec:
            time.sleep(self.rate_limit_sec - elapsed)
        self._last_call = time.time()

    @staticmethod
    def _market(code: str, market: Optional[Market]) -> Market:
        return market or resolve_market(code)

    def get_history(
        self,
        code: str,
        market: Optional[Market] = None,
        period: str = "6mo",
        interval: str = "1d",
    ) -> Optional[pd.DataFrame]:
        """
        ローソク足データ取得

        period   : 1d,5d,1mo,3mo,6mo,1y,2y,5y,10y,ytd,max
        interval : 1m,5m,15m,30m,60m,1d,1wk,1mo
                   （分足は直近60日のみ取得可能というYahoo側の制限あり）
        """
        mkt = self._market(code, market)
        self._throttle()
        try:
            ticker = yf.Ticker(mkt.ticker(code))
            df = ticker.history(period=period, interval=interval)
            if df.empty:
                log.warning(f"データなし: {code}")
                return None
            df.columns = [c.lower() for c in df.columns]
            return df
        except Exception as e:
            log.error(f"取得エラー {code}: {e}")
            return None

    def get_current_price(self, code: str, market: Optional[Market] = None) -> Optional[dict]:
        """現在値（直近終値）取得"""
        df = self.get_history(code, market, period="5d", interval="1d")
        if df is None or len(df) < 1:
            return None
        latest = df.iloc[-1]
        prev   = df.iloc[-2] if len(df) >= 2 else latest
        return {
            "code":    code,
            "price":   float(latest["close"]),
            "change":  float(latest["close"] - prev["close"]),
            "change_pct": float((latest["close"] - prev["close"]) / prev["close"] * 100),
            "volume":  int(latest["volume"]),
            "time":    df.index[-1].isoformat(),
        }

    def get_info(self, code: str, market: Optional[Market] = None) -> Optional[dict]:
        """銘柄基本情報（社名・配当利回り等）取得"""
        mkt = self._market(code, market)
        self._throttle()
        try:
            ticker = yf.Ticker(mkt.ticker(code))
            info = ticker.info
            return {
                "code":            code,
                "name":            info.get("longName") or info.get("shortName") or code,
                "sector":          info.get("sector", "-"),
                "dividend_yield":  info.get("dividendYield"),
                "pe_ratio":        info.get("trailingPE"),
                "pb_ratio":        info.get("priceToBook"),
                "market_cap":      info.get("marketCap"),
            }
        except Exception as e:
            log.error(f"情報取得エラー {code}: {e}")
            return None
