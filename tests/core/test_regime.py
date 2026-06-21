"""core.regime のテスト"""

import numpy as np
import pandas as pd
import pytest

from core.regime import (
    apply_regime_filters,
    check_adx_strength,
    check_index_regime,
    check_weekly_trend,
)

CFG = {
    "weekly_trend_filter": True,
    "index_ma": 50,
    "adx_min": 20,
}


# ──────────────────────────────────────────────────────────
# ヘルパー
# ──────────────────────────────────────────────────────────

def _make_weekly(n: int, above_ma: bool) -> pd.DataFrame:
    """週足の簡易 DataFrame。上昇トレンド/下落トレンドを指定。"""
    dates = pd.bdate_range("2020-01-06", periods=n, freq="W-FRI")
    if above_ma:
        closes = np.linspace(100, 200, n)  # 右肩上がり → MA(20) < 終値
    else:
        closes = np.linspace(200, 100, n)  # 右肩下がり → MA(20) > 終値
    return pd.DataFrame({"close": closes}, index=dates)


def _make_index(n: int, above_ma: bool) -> pd.DataFrame:
    """日足指数の簡易 DataFrame。"""
    dates = pd.bdate_range("2019-01-02", periods=n, freq="B")
    if above_ma:
        closes = np.linspace(20000, 30000, n)
    else:
        closes = np.linspace(30000, 20000, n)
    return pd.DataFrame({"close": closes}, index=dates)


def _make_daily_with_adx(n: int, adx_val: float) -> pd.DataFrame:
    """ADX 列を持つ日足 DataFrame のスタブ。"""
    dates = pd.bdate_range("2020-01-02", periods=n, freq="B")
    closes = np.linspace(1000, 1100, n)
    df = pd.DataFrame({"close": closes, "adx": [adx_val] * n}, index=dates)
    return df


# ──────────────────────────────────────────────────────────
# check_weekly_trend
# ──────────────────────────────────────────────────────────

class TestWeeklyTrend:
    def test_passes_when_close_above_ma20(self):
        df = _make_weekly(30, above_ma=True)
        t_date = df.index[-1]
        assert check_weekly_trend(df, t_date, CFG) is True

    def test_fails_when_close_below_ma20(self):
        df = _make_weekly(30, above_ma=False)
        t_date = df.index[-1]
        assert check_weekly_trend(df, t_date, CFG) is False

    def test_pass_when_df_none(self):
        assert check_weekly_trend(None, pd.Timestamp("2024-01-01"), CFG) is True

    def test_pass_when_data_insufficient(self):
        df = _make_weekly(15, above_ma=False)
        t_date = df.index[-1]
        # 20 本未満なら素通り
        assert check_weekly_trend(df, t_date, CFG) is True

    def test_pass_when_filter_disabled(self):
        cfg = {**CFG, "weekly_trend_filter": False}
        df = _make_weekly(30, above_ma=False)
        t_date = df.index[-1]
        assert check_weekly_trend(df, t_date, cfg) is True

    def test_uses_only_data_up_to_t_date(self):
        """t_date 以降のデータはフィルタに影響しない"""
        df_all = _make_weekly(30, above_ma=True)
        # 前半 25 本しかない時点で判定（上昇中）→ True
        t_date_mid = df_all.index[24]
        assert check_weekly_trend(df_all, t_date_mid, CFG) is True


# ──────────────────────────────────────────────────────────
# check_index_regime
# ──────────────────────────────────────────────────────────

class TestIndexRegime:
    def test_passes_when_index_above_ma50(self):
        df = _make_index(200, above_ma=True)
        t_date = df.index[-1]
        assert check_index_regime(df, t_date, CFG) is True

    def test_fails_when_index_below_ma50(self):
        df = _make_index(200, above_ma=False)
        t_date = df.index[-1]
        assert check_index_regime(df, t_date, CFG) is False

    def test_pass_when_df_none(self):
        assert check_index_regime(None, pd.Timestamp("2024-01-01"), CFG) is True

    def test_pass_when_data_insufficient(self):
        df = _make_index(30, above_ma=False)
        t_date = df.index[-1]
        assert check_index_regime(df, t_date, CFG) is True


# ──────────────────────────────────────────────────────────
# check_adx_strength
# ──────────────────────────────────────────────────────────

class TestAdxStrength:
    def test_passes_when_adx_above_min(self):
        df = _make_daily_with_adx(10, adx_val=25.0)
        assert check_adx_strength(df, CFG) is True

    def test_fails_when_adx_below_min(self):
        df = _make_daily_with_adx(10, adx_val=15.0)
        assert check_adx_strength(df, CFG) is False

    def test_passes_when_adx_equals_min(self):
        df = _make_daily_with_adx(10, adx_val=20.0)
        assert check_adx_strength(df, CFG) is True

    def test_pass_when_adx_column_missing(self):
        dates = pd.bdate_range("2020-01-02", periods=5, freq="B")
        df = pd.DataFrame({"close": [100] * 5}, index=dates)
        assert check_adx_strength(df, CFG) is True

    def test_pass_when_adx_is_nan(self):
        df = _make_daily_with_adx(10, adx_val=float("nan"))
        assert check_adx_strength(df, CFG) is True


# ──────────────────────────────────────────────────────────
# apply_regime_filters（統合）
# ──────────────────────────────────────────────────────────

class TestApplyRegimeFilters:
    def test_all_pass(self):
        df_daily = _make_daily_with_adx(10, adx_val=25.0)
        df_weekly = _make_weekly(30, above_ma=True)
        df_index = _make_index(200, above_ma=True)
        t_date = df_daily.index[-1]
        result = apply_regime_filters(df_daily, t_date, df_weekly, df_index, CFG)
        assert result["passed"] is True
        assert result["weekly_trend"] is True
        assert result["index_regime"] is True
        assert result["adx"] is True

    def test_fails_if_adx_low(self):
        df_daily = _make_daily_with_adx(10, adx_val=10.0)
        df_weekly = _make_weekly(30, above_ma=True)
        df_index = _make_index(200, above_ma=True)
        t_date = df_daily.index[-1]
        result = apply_regime_filters(df_daily, t_date, df_weekly, df_index, CFG)
        assert result["passed"] is False
        assert result["adx"] is False

    def test_fails_if_index_below_ma(self):
        df_daily = _make_daily_with_adx(10, adx_val=25.0)
        df_weekly = _make_weekly(30, above_ma=True)
        df_index = _make_index(200, above_ma=False)
        t_date = df_daily.index[-1]
        result = apply_regime_filters(df_daily, t_date, df_weekly, df_index, CFG)
        assert result["passed"] is False
        assert result["index_regime"] is False

    def test_passes_with_none_data(self):
        """週足も指数も None なら素通り（ADX は正常）"""
        df_daily = _make_daily_with_adx(10, adx_val=25.0)
        t_date = df_daily.index[-1]
        result = apply_regime_filters(df_daily, t_date, None, None, CFG)
        assert result["passed"] is True
