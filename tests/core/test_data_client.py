"""core.data_client のテスト（yfinance は mock）

yfinance は当日の未確定足を close=NaN の行として返すことがある。
この行を最新値に使うと現在値・含み損益が NaN になるため除去する挙動を検証する。
"""

from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd

from core.data_client import StockDataClient
from core.market import JP


def _df_with_trailing_nan():
    """末尾に close=NaN の未確定足を持つ日足 DataFrame。"""
    idx = pd.to_datetime(["2026-06-18", "2026-06-19", "2026-06-22"])
    return pd.DataFrame(
        {
            "Open":   [2800.0, 2790.0, 2780.0],
            "High":   [2820.0, 2800.0, 2790.0],
            "Low":    [2780.0, 2770.0, 2760.0],
            "Close":  [2793.5, 2776.5, np.nan],   # 末尾は未確定 → NaN
            "Volume": [27620900, 28789700, 22063700],
        },
        index=idx,
    )


def _patched_client(df):
    """yf.Ticker(...).history(...) が df を返す StockDataClient を作る。"""
    ticker = MagicMock()
    ticker.history.return_value = df
    return ticker


class TestGetHistoryDropsNaNClose:
    def test_trailing_nan_close_row_is_dropped(self):
        client = StockDataClient()
        with patch("core.data_client.yf.Ticker", return_value=_patched_client(_df_with_trailing_nan())):
            df = client.get_history("7203", JP, period="6mo", interval="1d")
        assert df is not None
        assert len(df) == 2                       # NaN行が除かれて2行
        assert df["close"].iloc[-1] == 2776.5     # 最新は直近の完成足
        assert df["close"].notna().all()

    def test_latest_close_is_finite_after_drop(self):
        client = StockDataClient()
        with patch("core.data_client.yf.Ticker", return_value=_patched_client(_df_with_trailing_nan())):
            df = client.get_history("7203", JP)
        price = float(df["close"].iloc[-1])
        assert price == price                     # NaN でない（含み損益が nan% にならない）

    def test_all_nan_close_returns_none(self):
        client = StockDataClient()
        idx = pd.to_datetime(["2026-06-22"])
        all_nan = pd.DataFrame(
            {"Open": [np.nan], "High": [np.nan], "Low": [np.nan],
             "Close": [np.nan], "Volume": [100]},
            index=idx,
        )
        with patch("core.data_client.yf.Ticker", return_value=_patched_client(all_nan)):
            df = client.get_history("7203", JP)
        assert df is None

    def test_valid_data_is_returned_unchanged(self):
        client = StockDataClient()
        idx = pd.to_datetime(["2026-06-18", "2026-06-19"])
        clean = pd.DataFrame(
            {"Open": [2800.0, 2790.0], "High": [2820.0, 2800.0],
             "Low": [2780.0, 2770.0], "Close": [2793.5, 2776.5],
             "Volume": [27620900, 28789700]},
            index=idx,
        )
        with patch("core.data_client.yf.Ticker", return_value=_patched_client(clean)):
            df = client.get_history("7203", JP)
        assert len(df) == 2
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
