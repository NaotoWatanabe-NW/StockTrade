"""data.price_cache のテスト

yfinance は monkeypatch で差し替え、DB の読み書きだけを検証する。
"""

import sqlite3
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from data.db import get_connection, init_schema
from data.price_cache import get_history_cached, _needs_refresh
from core.market import JP


@pytest.fixture
def conn(tmp_path):
    """テスト用インメモリ代替 DB（tmp_path の実ファイル）"""
    db_file = str(tmp_path / "test.db")
    c = sqlite3.connect(db_file)
    c.row_factory = sqlite3.Row
    init_schema(c)
    yield c
    c.close()


def _sample_df(days=10):
    """単純な OHLCV DataFrame（DatetimeIndex）。直近 days 営業日分を返す。"""
    from datetime import date, timedelta
    end = date.today()
    start = end - timedelta(days=days * 2)  # 営業日換算で余裕を持つ
    idx = pd.bdate_range(str(start), periods=days)
    return pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0},
        index=idx,
    )


class TestNeedsRefresh:
    def test_empty_table_needs_refresh(self, conn):
        assert _needs_refresh(conn, "7203", "1d", date(2019, 1, 1), date.today()) is True

    def test_sufficient_data_does_not_need_refresh(self, conn):
        # 十分なデータをDBに入れておく
        today = date.today()
        since = today - timedelta(days=365 * 5)
        # oldest が since の直後、newest が今日 → リフレッシュ不要
        conn.execute(
            "INSERT OR REPLACE INTO price_history (code, interval, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?,?)",
            ("7203", "1d", str(since), 100, 101, 99, 100, 1000),
        )
        conn.execute(
            "INSERT OR REPLACE INTO price_history (code, interval, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?,?)",
            ("7203", "1d", str(today), 100, 101, 99, 100, 1000),
        )
        conn.commit()
        assert _needs_refresh(conn, "7203", "1d", since, today) is False

    def test_stale_data_needs_refresh(self, conn):
        today = date.today()
        since = today - timedelta(days=365 * 5)
        old_date = today - timedelta(days=10)  # 10日前が最新 → 要更新
        conn.execute(
            "INSERT OR REPLACE INTO price_history (code, interval, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?,?)",
            ("7203", "1d", str(since), 100, 101, 99, 100, 1000),
        )
        conn.execute(
            "INSERT OR REPLACE INTO price_history (code, interval, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?,?)",
            ("7203", "1d", str(old_date), 100, 101, 99, 100, 1000),
        )
        conn.commit()
        assert _needs_refresh(conn, "7203", "1d", since, today) is True


class TestGetHistoryCached:
    def test_cache_miss_fetches_from_yfinance_and_stores(self, conn):
        """DBに無い場合は yfinance から取得して保存し、DataFrameを返す"""
        df_mock = _sample_df()
        with patch("data.price_cache._fetch_yfinance", return_value=df_mock) as mock_fetch:
            result = get_history_cached(conn, "7203", JP, interval="1d", years=1)

        mock_fetch.assert_called_once()
        assert result is not None
        assert len(result) == len(df_mock)
        assert list(result.columns) == ["open", "high", "low", "close", "volume"]

    def test_cache_hit_skips_yfinance(self, conn):
        """DBに十分なデータがある場合は yfinance を呼ばない"""
        today = date.today()
        since = today - timedelta(days=365)
        # DBに1年分のデータを入れておく
        conn.execute(
            "INSERT OR REPLACE INTO price_history (code, interval, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?,?)",
            ("7203", "1d", str(since), 100, 101, 99, 100, 1000),
        )
        conn.execute(
            "INSERT OR REPLACE INTO price_history (code, interval, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?,?)",
            ("7203", "1d", str(today), 100, 101, 99, 100, 1000),
        )
        conn.commit()

        with patch("data.price_cache._fetch_yfinance") as mock_fetch:
            result = get_history_cached(conn, "7203", JP, interval="1d", years=1)

        mock_fetch.assert_not_called()

    def test_yfinance_failure_returns_none_when_no_cache(self, conn):
        """yfinance が失敗してDBにも何もない場合は None を返す"""
        with patch("data.price_cache._fetch_yfinance", return_value=None):
            result = get_history_cached(conn, "9999", JP, interval="1d", years=1)
        assert result is None


class TestNaNCloseHandling:
    """当日の未確定足（close=NaN）を保存・返却しないことを検証する。"""

    def test_fetch_drops_trailing_nan_close_row(self, conn):
        """_fetch_yfinance は close=NaN 行を除いて保存し、有効な足だけ返す。"""
        import numpy as np
        idx = pd.bdate_range(str(date.today() - timedelta(days=4)), periods=3)
        raw = pd.DataFrame(
            {"Open": [100.0, 101.0, 102.0], "High": [101.0, 102.0, 103.0],
             "Low": [99.0, 100.0, 101.0], "Close": [100.0, 101.0, np.nan],
             "Volume": [1000, 1100, 1200]},
            index=idx,
        )
        ticker = MagicMock()
        ticker.history.return_value = raw
        with patch("data.price_cache.yf.Ticker", return_value=ticker):
            result = get_history_cached(conn, "7203", JP, interval="1d", years=1)
        assert result is not None
        assert result["close"].notna().all()
        assert result["close"].iloc[-1] == 101.0   # 未確定足は含まれない

    def test_load_excludes_preexisting_null_close_rows(self, conn):
        """既にDBに入っている終値NULL行は読み出し時に除外される。"""
        today = date.today()
        since = today - timedelta(days=365)
        conn.execute(
            "INSERT OR REPLACE INTO price_history (code, interval, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?,?)",
            ("7203", "1d", str(since), 100, 101, 99, 100, 1000),
        )
        # 終値 NULL の未確定足が過去に取り込まれていたケース
        conn.execute(
            "INSERT OR REPLACE INTO price_history (code, interval, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?,?)",
            ("7203", "1d", str(today), None, None, None, None, 1200),
        )
        conn.commit()
        with patch("data.price_cache._fetch_yfinance") as mock_fetch:
            result = get_history_cached(conn, "7203", JP, interval="1d", years=1)
        mock_fetch.assert_not_called()              # キャッシュヒット（最新日=today）
        assert result is not None
        assert result["close"].notna().all()         # NULL行は除外
        assert len(result) == 1
