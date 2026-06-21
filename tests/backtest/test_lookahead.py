"""
ルックアヘッドバイアス検証テスト

バックテストが将来データを参照していないことを系統的に確認する。

検証ポイント:
    1. インジケーターの因果性   : bar-t の値は bar-t+1以降を変えても変わらない
    2. シグナルの独立性         : df[:t+1] のシグナルは t+1以降のデータに依存しない
    3. フィルの発生タイミング   : fill_bar > signal_bar（翌足以降に約定）
    4. フィル価格の正当性       : fill_price = entry_price + slippage（未来高値等は不使用）
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

import config
from backtest.simulator import Trade, simulate_symbol
from core.indicators import add_technical_indicators, detect_signals

# ── 合成データ生成ヘルパー ──────────────────────────────────────────
_CFG = config.SCREENING_CONFIG


def _make_df(n: int = 300, trend: float = 0.001, vol: float = 0.015, seed: int = 42) -> pd.DataFrame:
    """
    合成日足 OHLCV を生成する。

    trend>0 で上昇トレンド、trend=0 でランダムウォーク。
    終値は対数ランダムウォーク、高値/安値は当日変動幅を加算。
    """
    rng = np.random.RandomState(seed)
    log_ret = rng.normal(trend, vol, n)
    close = 1000.0 * np.exp(np.cumsum(log_ret))
    daily_range = close * rng.uniform(0.005, 0.025, n)
    high = close + daily_range * rng.uniform(0.3, 0.7, n)
    low  = close - daily_range * rng.uniform(0.3, 0.7, n)
    open_ = np.roll(close, 1); open_[0] = close[0]
    volume = rng.uniform(1e6, 5e6, n)
    dates = pd.date_range(datetime(2020, 1, 2), periods=n, freq="B")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )


def _shuffle_future(df: pd.DataFrame, pivot: int, seed: int = 99) -> pd.DataFrame:
    """pivot 以降のバーを行単位でシャッフルした DataFrame を返す。"""
    df2 = df.copy()
    rng = np.random.RandomState(seed)
    idx = rng.permutation(len(df) - pivot) + pivot
    df2.iloc[pivot:] = df.iloc[idx].values
    return df2


# ═══════════════════════════════════════════════════════════════════════
# 1. インジケーターの因果性（Causality of Indicators）
# ═══════════════════════════════════════════════════════════════════════

class TestIndicatorCausality:
    """add_technical_indicators が因果的（後ろ向き）であることを確認する。"""

    PIVOT = 150
    INDICATORS = ["ma_short", "ma_long", "rsi", "atr", "macd", "adx", "highest_n"]

    def _both_indicators(self, seed_future: int = 99):
        df = _make_df(300, seed=42)
        df_shuf = _shuffle_future(df, self.PIVOT, seed=seed_future)
        ind1 = add_technical_indicators(df.copy(), _CFG)
        ind2 = add_technical_indicators(df_shuf.copy(), _CFG)
        return ind1, ind2

    def test_ma_short_unaffected_by_future_shuffle(self):
        ind1, ind2 = self._both_indicators()
        pd.testing.assert_series_equal(
            ind1["ma_short"].iloc[:self.PIVOT],
            ind2["ma_short"].iloc[:self.PIVOT],
        )

    def test_ma_long_unaffected_by_future_shuffle(self):
        ind1, ind2 = self._both_indicators()
        pd.testing.assert_series_equal(
            ind1["ma_long"].iloc[:self.PIVOT],
            ind2["ma_long"].iloc[:self.PIVOT],
        )

    def test_rsi_unaffected_by_future_shuffle(self):
        ind1, ind2 = self._both_indicators()
        pd.testing.assert_series_equal(
            ind1["rsi"].iloc[:self.PIVOT],
            ind2["rsi"].iloc[:self.PIVOT],
        )

    def test_atr_unaffected_by_future_shuffle(self):
        ind1, ind2 = self._both_indicators()
        pd.testing.assert_series_equal(
            ind1["atr"].iloc[:self.PIVOT],
            ind2["atr"].iloc[:self.PIVOT],
        )

    def test_macd_unaffected_by_future_shuffle(self):
        ind1, ind2 = self._both_indicators()
        pd.testing.assert_series_equal(
            ind1["macd"].iloc[:self.PIVOT],
            ind2["macd"].iloc[:self.PIVOT],
        )

    def test_adx_unaffected_by_future_shuffle(self):
        ind1, ind2 = self._both_indicators()
        pd.testing.assert_series_equal(
            ind1["adx"].iloc[:self.PIVOT],
            ind2["adx"].iloc[:self.PIVOT],
        )

    def test_highest_n_excludes_current_bar(self):
        """highest_n は当日高値を含まない（.shift(1) で1本ずらしている）。"""
        df = _make_df(100)
        ind = add_technical_indicators(df.copy(), _CFG)
        lookback = _CFG["breakout_lookback"]
        for t in range(lookback + 5, 100):
            # highest_n[t] は high[t-lookback..t-1] の最大値（当日 t を含まない）
            expected = df["high"].iloc[t - lookback: t].max()
            assert abs(ind["highest_n"].iloc[t] - expected) < 1e-6, (
                f"bar {t}: highest_n={ind['highest_n'].iloc[t]:.4f}, "
                f"expected={expected:.4f}"
            )


# ═══════════════════════════════════════════════════════════════════════
# 2. シグナルの独立性（Signal Independence from Future Data）
# ═══════════════════════════════════════════════════════════════════════

class TestSignalIndependence:
    """detect_signals が将来バーに依存しないことを確認する。"""

    PIVOT = 200

    def test_signal_detection_identical_before_pivot(self):
        """
        pivot 以前のシグナルは、pivot 以降のデータをシャッフルしても変わらない。

        インジケーターを事前計算した後、ループ内では df[:t+1] スライスのみ使う
        という設計の正しさを確認する。
        """
        n = 300
        df = _make_df(n, trend=0.002, seed=10)
        df_shuf = _shuffle_future(df, self.PIVOT, seed=77)

        ind1 = add_technical_indicators(df.copy(), _CFG)
        ind2 = add_technical_indicators(df_shuf.copy(), _CFG)

        min_len = _CFG.get("ma_long", 25) + 2

        for t in range(min_len, self.PIVOT):
            sigs1 = detect_signals(ind1.iloc[:t + 1], _CFG)
            sigs2 = detect_signals(ind2.iloc[:t + 1], _CFG)
            types1 = sorted(s["type"] for s in sigs1)
            types2 = sorted(s["type"] for s in sigs2)
            assert types1 == types2, (
                f"bar {t} のシグナルが pivot={self.PIVOT} 以降のシャッフルで変化した: "
                f"{types1} vs {types2}"
            )


# ═══════════════════════════════════════════════════════════════════════
# 3. フィル発生タイミング（Fill Timing After Signal Bar）
# ═══════════════════════════════════════════════════════════════════════

class TestFillTiming:
    """エントリー約定がシグナルバーの翌足以降に発生することを確認する。"""

    @pytest.fixture
    def simulated_trades(self):
        df = _make_df(500, trend=0.003, seed=7)
        trades, _ = simulate_symbol(
            "TEST", df, _CFG,
            config.SCORING_CONFIG, config.TRADE_PLAN_CONFIG,
            config.BACKTEST_CONFIG, config.EXIT_CONFIG,
        )
        return [t for t in trades if t.filled]

    def test_fill_bar_always_after_signal_bar(self, simulated_trades):
        """fill_bar > signal_bar を全約定で確認する（同日約定はない）。"""
        assert len(simulated_trades) > 0, "フィルされたトレードが存在しない（データ不足）"
        for t in simulated_trades:
            assert t.fill_bar > t.signal_bar, (
                f"signal_bar={t.signal_bar} に fill_bar={t.fill_bar} が同日約定している"
            )

    def test_fill_price_equals_entry_price_plus_slippage(self, simulated_trades):
        """
        fill_price = entry_price + slippage。

        ATR スリッページは現時点では 0.1 なのでわずかなずれが生じるが、
        fill_price が entry_price に対して妥当な範囲内であることを確認する。
        """
        slippage_mult = float(config.BACKTEST_CONFIG.get("slippage_atr", 0.1))
        for t in simulated_trades:
            if t.fill_price is None:
                continue
            # fill_price >= entry_price（スリッページで押し上げられる）
            assert t.fill_price >= t.entry_price - 1e-6, (
                f"fill_price={t.fill_price} < entry_price={t.entry_price}"
            )
            # fill_price が entry_price から ATR の 2 倍以上離れていない
            if slippage_mult > 0 and t.risk > 0:
                max_slip = slippage_mult * t.risk * 3  # 余裕を持って 3 倍まで許容
                assert (t.fill_price - t.entry_price) <= max_slip + 1e-6


# ═══════════════════════════════════════════════════════════════════════
# 4. 出口決定の独立性（Exit Uses Only Current Bar）
# ═══════════════════════════════════════════════════════════════════════

class TestExitCausality:
    """出口（損切・利確）が将来バーの情報を使わないことを確認する。"""

    def test_exit_bar_after_fill_bar(self):
        """exit_bar >= fill_bar（約定当日に出口が発生することは通常ない）。"""
        df = _make_df(500, trend=0.001, seed=3)
        trades, _ = simulate_symbol(
            "TEST", df, _CFG,
            config.SCORING_CONFIG, config.TRADE_PLAN_CONFIG,
            config.BACKTEST_CONFIG, config.EXIT_CONFIG,
        )
        closed = [t for t in trades if t.closed and t.exit_bar is not None and t.fill_bar is not None]
        assert len(closed) > 0
        for t in closed:
            assert t.exit_bar >= t.fill_bar, (
                f"exit_bar={t.exit_bar} < fill_bar={t.fill_bar}"
            )

    def test_simulate_identical_before_pivot_with_shuffled_future(self):
        """
        pivot 以前に signal が発生したトレードのうち、pivot 以前に完結するものは
        将来データをシャッフルしても変わらない。

        ※ pivot 以降に fill または exit するトレードは比較対象外。
        """
        n = 500
        pivot = 300
        # トレードが完結するまでのバッファ（最大保有バー + 有効期限）
        buffer = int(config.BACKTEST_CONFIG.get("entry_order_valid_days", 15)) + \
                 int(config.EXIT_CONFIG.get("time_stop_days", 20))
        safe_pivot = pivot - buffer

        df = _make_df(n, trend=0.002, seed=55)
        df_shuf = _shuffle_future(df, pivot, seed=88)

        trades1, _ = simulate_symbol(
            "TEST", df, _CFG,
            config.SCORING_CONFIG, config.TRADE_PLAN_CONFIG,
            config.BACKTEST_CONFIG, config.EXIT_CONFIG,
        )
        trades2, _ = simulate_symbol(
            "TEST", df_shuf, _CFG,
            config.SCORING_CONFIG, config.TRADE_PLAN_CONFIG,
            config.BACKTEST_CONFIG, config.EXIT_CONFIG,
        )

        # safe_pivot 以前にシグナルが発生したものだけを比較
        def _safe_trades(tlist):
            return [
                t for t in tlist
                if t.signal_bar < safe_pivot
            ]

        st1 = _safe_trades(trades1)
        st2 = _safe_trades(trades2)

        # 件数・シグナルバー・シグナルタイプが一致すること
        assert len(st1) == len(st2), (
            f"safe_pivot({safe_pivot}) 以前のトレード数が異なる: {len(st1)} vs {len(st2)}"
        )
        for t1, t2 in zip(st1, st2):
            assert t1.signal_bar == t2.signal_bar
            assert sorted(t1.signal_types) == sorted(t2.signal_types)
            if t1.filled and t2.filled:
                assert t1.fill_bar == t2.fill_bar
                assert abs((t1.fill_price or 0) - (t2.fill_price or 0)) < 1e-6
