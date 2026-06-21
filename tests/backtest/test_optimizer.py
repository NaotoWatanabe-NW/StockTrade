"""backtest.optimizer のテスト"""

import numpy as np
import pandas as pd
import pytest

from backtest.optimizer import (
    WalkForwardResult,
    WindowResult,
    _apply_params,
    _grid_combinations,
    _make_windows,
    _objective,
    _slice_df,
    run_walk_forward,
)
from backtest.metrics import compute_metrics


# ──────────────────────────────────────────────────────────
# ヘルパー
# ──────────────────────────────────────────────────────────

def _screening_cfg():
    return {
        "volume_spike_ratio": 2.0, "volume_avg_period": 20,
        "ma_short": 5, "ma_long": 25, "rsi_period": 14,
        "rsi_oversold": 30, "rsi_overbought": 70,
        "breakout_lookback": 20,
        "min_price": 0, "min_avg_volume": 0,
    }


def _exit_cfg():
    return {
        "time_stop_days": 15, "partial_tp_r": 1.0, "partial_tp_pct": 0.5,
        "move_to_breakeven": True, "trail_atr_mult": 2.0,
    }


def _backtest_cfg():
    return {
        "entry_order_valid_days": 15, "slippage_atr": 0.0, "min_abs_score": 0,
    }


def _make_ohlcv(n: int, start: str = "2019-01-02") -> pd.DataFrame:
    """ランダムウォーク風の OHLCV DataFrame を生成する。"""
    rng = np.random.default_rng(42)
    dates = pd.bdate_range(start, periods=n, freq="B")
    close = 1000 + np.cumsum(rng.normal(0, 10, n))
    high  = close + rng.uniform(5, 20, n)
    low   = close - rng.uniform(5, 20, n)
    open_ = close + rng.normal(0, 5, n)
    vol   = rng.integers(500_000, 2_000_000, n).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
        index=dates,
    )


# ──────────────────────────────────────────────────────────
# _grid_combinations
# ──────────────────────────────────────────────────────────

class TestGridCombinations:
    def test_single_param(self):
        grid = {"a": [1, 2, 3]}
        combos = _grid_combinations(grid)
        assert len(combos) == 3
        assert {"a": 1} in combos

    def test_two_params(self):
        grid = {"a": [1, 2], "b": [10, 20]}
        combos = _grid_combinations(grid)
        assert len(combos) == 4
        assert {"a": 1, "b": 10} in combos
        assert {"a": 2, "b": 20} in combos

    def test_empty_grid(self):
        assert _grid_combinations({}) == [{}]


# ──────────────────────────────────────────────────────────
# _apply_params
# ──────────────────────────────────────────────────────────

class TestApplyParams:
    def test_updates_backtest_cfg(self):
        scr, bt, ex = _apply_params(
            _screening_cfg(), _backtest_cfg(), _exit_cfg(),
            {"min_abs_score": 30}
        )
        assert bt["min_abs_score"] == 30

    def test_updates_exit_cfg(self):
        scr, bt, ex = _apply_params(
            _screening_cfg(), _backtest_cfg(), _exit_cfg(),
            {"trail_atr_mult": 1.5, "partial_tp_r": 0.8}
        )
        assert ex["trail_atr_mult"] == 1.5
        assert ex["partial_tp_r"] == 0.8

    def test_updates_screening_cfg(self):
        scr, bt, ex = _apply_params(
            _screening_cfg(), _backtest_cfg(), _exit_cfg(),
            {"breakout_lookback": 30}
        )
        assert scr["breakout_lookback"] == 30

    def test_does_not_mutate_original(self):
        orig_scr = _screening_cfg()
        orig_bt  = _backtest_cfg()
        orig_ex  = _exit_cfg()
        _apply_params(orig_scr, orig_bt, orig_ex, {
            "min_abs_score": 99, "trail_atr_mult": 9.9, "breakout_lookback": 99,
        })
        assert orig_bt["min_abs_score"] == 0
        assert orig_ex["trail_atr_mult"] == 2.0
        assert orig_scr["breakout_lookback"] == 20


# ──────────────────────────────────────────────────────────
# _make_windows
# ──────────────────────────────────────────────────────────

class TestMakeWindows:
    def test_generates_correct_number_of_windows(self):
        # 5年データ、IS=3y、OOS=1y、step=1y → 2ウィンドウ
        earliest = pd.Timestamp("2019-01-01")
        latest   = pd.Timestamp("2024-01-01")
        wins = _make_windows(earliest, latest, is_years=3, oos_years=1, step_years=1)
        assert len(wins) == 2

    def test_windows_do_not_overlap_oos(self):
        earliest = pd.Timestamp("2019-01-01")
        latest   = pd.Timestamp("2024-01-01")
        wins = _make_windows(earliest, latest, is_years=3, oos_years=1, step_years=1)
        # OOS 期間が重ならないこと
        assert wins[0]["oos_end"] <= wins[1]["oos_start"]

    def test_empty_when_data_too_short(self):
        # 2年しかデータがなく IS=3 を要求 → ウィンドウなし
        earliest = pd.Timestamp("2022-01-01")
        latest   = pd.Timestamp("2024-01-01")
        wins = _make_windows(earliest, latest, is_years=3, oos_years=1, step_years=1)
        assert wins == []


# ──────────────────────────────────────────────────────────
# _slice_df
# ──────────────────────────────────────────────────────────

class TestSliceDF:
    def test_slices_correct_date_range(self):
        df = _make_ohlcv(500, start="2020-01-02")
        start = pd.Timestamp("2021-01-01")
        end   = pd.Timestamp("2022-01-01")
        sliced = _slice_df(df, start, end)
        assert (sliced.index >= start).all()
        assert (sliced.index < end).all()

    def test_returns_only_ohlcv_columns(self):
        df = _make_ohlcv(100)
        sliced = _slice_df(df, df.index[0], df.index[-1])
        assert set(sliced.columns) == {"open", "high", "low", "close", "volume"}


# ──────────────────────────────────────────────────────────
# _objective
# ──────────────────────────────────────────────────────────

class TestObjective:
    def _m(self, win_rate=0.6, avg_r=0.3, pf=1.5, closed=20):
        return {
            "win_rate": win_rate, "avg_r": avg_r,
            "profit_factor": pf, "closed": closed,
        }

    def test_profit_factor_objective(self):
        m = self._m(pf=1.8)
        assert _objective(m, "profit_factor") == pytest.approx(1.8)

    def test_avg_r_objective(self):
        m = self._m(avg_r=0.25)
        assert _objective(m, "avg_r") == pytest.approx(0.25)

    def test_win_rate_objective(self):
        m = self._m(win_rate=0.65)
        assert _objective(m, "win_rate") == pytest.approx(0.65)

    def test_zero_when_no_closed_trades(self):
        m = self._m(closed=0)
        assert _objective(m, "profit_factor") == 0.0

    def test_inf_profit_factor_is_capped(self):
        m = self._m(pf=float("inf"), closed=5)
        assert _objective(m, "profit_factor") == pytest.approx(10.0)


# ──────────────────────────────────────────────────────────
# run_walk_forward（統合テスト・軽量グリッド）
# ──────────────────────────────────────────────────────────

class TestRunWalkForward:
    def _minimal_optimize_cfg(self):
        return {
            "is_years":  2,
            "oos_years": 1,
            "step_years": 1,
            "objective": "profit_factor",
            "param_grid": {
                "min_abs_score":  [0, 20],     # 2 × 2 = 4 組み合わせ
                "trail_atr_mult": [1.5, 2.0],
            },
        }

    def test_returns_walk_forward_result(self):
        df = _make_ohlcv(1000, "2019-01-02")
        result = run_walk_forward(
            universe_dfs={"TEST": df},
            screening_cfg=_screening_cfg(),
            scoring_cfg={
                "weights": {"trend":0.3,"macd":0.2,"rsi":0.15,"volume":0.15,"breakout":0.2},
                "thresholds": {"strong":60,"weak":20},
                "rsi_low": 30, "rsi_high": 70, "ma_slope_lookback": 10, "min_abs_score": 0,
            },
            trade_plan_cfg={
                "atr_entry_pullback": 0.5, "atr_stop_mult": 2.0, "reward_risk_ratio": 2.0,
            },
            backtest_cfg=_backtest_cfg(),
            exit_cfg=_exit_cfg(),
            optimize_cfg=self._minimal_optimize_cfg(),
        )
        assert isinstance(result, WalkForwardResult)
        assert len(result.windows) >= 1

    def test_recommended_params_come_from_last_window(self):
        df = _make_ohlcv(1000, "2019-01-02")
        result = run_walk_forward(
            universe_dfs={"TEST": df},
            screening_cfg=_screening_cfg(),
            scoring_cfg={
                "weights": {"trend":0.3,"macd":0.2,"rsi":0.15,"volume":0.15,"breakout":0.2},
                "thresholds": {"strong":60,"weak":20},
                "rsi_low": 30, "rsi_high": 70, "ma_slope_lookback": 10, "min_abs_score": 0,
            },
            trade_plan_cfg={
                "atr_entry_pullback": 0.5, "atr_stop_mult": 2.0, "reward_risk_ratio": 2.0,
            },
            backtest_cfg=_backtest_cfg(),
            exit_cfg=_exit_cfg(),
            optimize_cfg=self._minimal_optimize_cfg(),
        )
        last_window = result.windows[-1]
        assert result.recommended_params == last_window.best_params

    def test_raises_when_data_too_short(self):
        df = _make_ohlcv(100, "2023-01-02")  # 1年未満
        with pytest.raises(ValueError, match="ウィンドウが生成できません"):
            run_walk_forward(
                universe_dfs={"X": df},
                screening_cfg=_screening_cfg(),
                scoring_cfg={
                    "weights": {"trend":0.3,"macd":0.2,"rsi":0.15,"volume":0.15,"breakout":0.2},
                    "thresholds": {"strong":60,"weak":20},
                    "rsi_low": 30, "rsi_high": 70, "ma_slope_lookback": 10, "min_abs_score": 0,
                },
                trade_plan_cfg={
                    "atr_entry_pullback": 0.5, "atr_stop_mult": 2.0, "reward_risk_ratio": 2.0,
                },
                backtest_cfg=_backtest_cfg(),
                exit_cfg=_exit_cfg(),
                optimize_cfg=self._minimal_optimize_cfg(),
            )
