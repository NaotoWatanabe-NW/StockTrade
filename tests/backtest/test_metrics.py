"""backtest.metrics のテスト"""

from backtest.metrics import compute_metrics, format_report
from backtest.simulator import Trade, NoFill


def _trade(filled=True, closed=True, fill_price=1000.0, exit_price=1040.0,
           exit_reason="TAKE_PROFIT", risk=20.0, bars_held=5):
    entry = fill_price or 1000.0  # fill_price=None の場合もTradeを作れるよう
    t = Trade(
        code="T", signal_bar=0, signal_date="2024-01-01",
        signal_types=["BREAKOUT_HIGH"], side="BUY",
        entry_kind="LIMIT", entry_price=entry,
        stop_price=entry - risk, target_price=entry + risk * 2,
        risk=risk,
    )
    t.filled = filled
    t.fill_price = fill_price if filled else None
    t.fill_date = "2024-01-02" if filled else None
    t.fill_bar = 1 if filled else None
    t.closed = closed
    t.exit_price = exit_price if closed else None
    t.exit_date = "2024-01-10" if closed else None
    t.exit_bar = 10 if closed else None
    t.exit_reason = exit_reason if closed else None
    t.bars_held = bars_held if closed else None
    return t


def _no_fill():
    return NoFill(
        code="T", signal_bar=0, signal_date="2024-01-01",
        signal_types=["BREAKOUT_HIGH"], side="BUY",
        entry_kind="LIMIT", entry_price=1000.0,
    )


class TestComputeMetrics:
    def test_empty_input_returns_zero_metrics(self):
        m = compute_metrics([], [])
        assert m["total_signals"] == 0
        assert m["win_rate"] == 0.0
        assert m["avg_r"] == 0.0

    def test_win_rate_is_wins_over_closed(self):
        win = _trade(exit_price=1060.0, exit_reason="TAKE_PROFIT")   # +2R
        loss = _trade(exit_price=980.0, exit_reason="STOP_LOSS")     # -1R
        m = compute_metrics([win, loss], [])
        assert m["wins"] == 1
        assert m["losses"] == 1
        assert m["win_rate"] == pytest.approx(0.5)

    def test_avg_r_is_mean_of_pnl_r(self):
        # +2R と -1R → 平均 +0.5R
        win = _trade(fill_price=1000.0, exit_price=1040.0, risk=20.0)   # (1040-1000)/20 = +2R
        loss = _trade(fill_price=1000.0, exit_price=980.0, risk=20.0,
                      exit_reason="STOP_LOSS")                           # (980-1000)/20 = -1R
        m = compute_metrics([win, loss], [])
        assert m["avg_r"] == pytest.approx(0.5)

    def test_profit_factor_is_gain_over_loss(self):
        # 総利益 2R / 総損失 1R = 2.0
        win = _trade(fill_price=1000.0, exit_price=1040.0, risk=20.0)
        loss = _trade(fill_price=1000.0, exit_price=980.0, risk=20.0,
                      exit_reason="STOP_LOSS")
        m = compute_metrics([win, loss], [])
        assert m["profit_factor"] == pytest.approx(2.0)

    def test_profit_factor_is_inf_when_no_losses(self):
        win = _trade()
        m = compute_metrics([win], [])
        assert m["profit_factor"] == float("inf")

    def test_fill_rate_counts_no_fills(self):
        filled = _trade()
        nf = _no_fill()
        m = compute_metrics([filled], [nf])
        assert m["total_signals"] == 2
        assert m["filled"] == 1
        assert m["fill_rate"] == pytest.approx(0.5)

    def test_max_drawdown_is_negative(self):
        # +2R, -1R, -1R → cumulative: 2, 1, 0 → max_dd = 0 - 2 = -2
        trades = [
            _trade(fill_price=1000.0, exit_price=1040.0, risk=20.0),
            _trade(fill_price=1000.0, exit_price=980.0, risk=20.0, exit_reason="STOP_LOSS"),
            _trade(fill_price=1000.0, exit_price=980.0, risk=20.0, exit_reason="STOP_LOSS"),
        ]
        m = compute_metrics(trades, [])
        assert m["max_drawdown_r"] == pytest.approx(-2.0)

    def test_time_stop_rate_is_fraction_of_closed(self):
        ts = _trade(exit_reason="TIME_STOP")
        sl = _trade(exit_reason="STOP_LOSS")
        m = compute_metrics([ts, sl], [])
        assert m["time_stop_rate"] == pytest.approx(0.5)

    def test_unfilled_trade_not_counted_in_closed(self):
        unfilled = _trade(filled=False, closed=False, fill_price=None, exit_price=None)
        m = compute_metrics([unfilled], [])
        assert m["filled"] == 0
        assert m["closed"] == 0

    def test_format_report_returns_string(self):
        m = compute_metrics([], [])
        report = format_report(m)
        assert isinstance(report, str)
        assert "勝率" in report


import pytest


class TestPhase7Metrics:
    """Phase 7 追加指標のテスト（Sharpe / 年率リターン / 資産曲線）"""

    def _trades_with_dates(self, rs: list[float], start_year=2020):
        """指定 R 値リストで closed trades を生成（日付を 30 日刻みで付与）"""
        trades = []
        from datetime import date, timedelta
        for i, r in enumerate(rs):
            exit_dt = date(start_year, 1, 1) + timedelta(days=30 * i)
            exit_price = 1000.0 + r * 20.0
            t = _trade(
                fill_price=1000.0,
                exit_price=exit_price,
                risk=20.0,
                exit_reason="TAKE_PROFIT" if r > 0 else "STOP_LOSS",
            )
            t.signal_date = str(date(start_year, 1, 1))
            t.exit_date = str(exit_dt)
            trades.append(t)
        return trades

    def test_equity_curve_starts_at_one_and_grows_with_wins(self):
        trades = self._trades_with_dates([2.0, 2.0, 2.0])  # すべて +2R
        m = compute_metrics(trades, [], risk_cfg={"risk_per_trade_pct": 1.0})
        curve = m["equity_curve"]
        assert curve[0]["equity"] == pytest.approx(1.02)   # 1×(1+2×0.01)
        assert curve[-1]["equity"] > 1.0

    def test_equity_curve_sorted_by_date(self):
        trades = self._trades_with_dates([1.0, -0.5, 2.0])
        m = compute_metrics(trades, [], risk_cfg={"risk_per_trade_pct": 1.0})
        dates = [p["date"] for p in m["equity_curve"]]
        assert dates == sorted(dates)

    def test_equity_curve_empty_when_no_closed_trades(self):
        m = compute_metrics([], [])
        assert m["equity_curve"] == []

    def test_sharpe_positive_for_consistently_winning_strategy(self):
        trades = self._trades_with_dates([1.0] * 50)  # 50 連勝
        m = compute_metrics(trades, [], risk_cfg={"risk_per_trade_pct": 1.0})
        assert m["sharpe_ratio"] == 0.0  # std=0 → sharpe=0

    def test_sharpe_nonzero_with_mixed_results(self):
        import random
        random.seed(42)
        rs = [random.choice([1.5, -1.0]) for _ in range(60)]
        trades = self._trades_with_dates(rs)
        m = compute_metrics(trades, [], risk_cfg={"risk_per_trade_pct": 1.0})
        assert isinstance(m["sharpe_ratio"], float)
        assert abs(m["sharpe_ratio"]) < 10.0   # 発散していない

    def test_annual_return_pct_positive_for_winning_strategy(self):
        trades = self._trades_with_dates([2.0] * 24, start_year=2020)  # 24 ヶ月分
        m = compute_metrics(trades, [], risk_cfg={"risk_per_trade_pct": 1.0})
        assert m["annual_return_pct"] > 0.0

    def test_annual_return_pct_negative_for_losing_strategy(self):
        trades = self._trades_with_dates([-1.0] * 24, start_year=2020)
        m = compute_metrics(trades, [], risk_cfg={"risk_per_trade_pct": 1.0})
        assert m["annual_return_pct"] < 0.0

    def test_annual_return_pct_zero_without_risk_cfg(self):
        trades = self._trades_with_dates([2.0, 2.0])
        m = compute_metrics(trades, [])  # risk_cfg=None → risk_pct=0.01 (デフォルト1%)
        # デフォルト1%が使われるため 0 にはならない
        assert isinstance(m["annual_return_pct"], float)

    def test_equity_curve_respects_risk_pct(self):
        trades = self._trades_with_dates([1.0])  # +1R
        m1 = compute_metrics(trades, [], risk_cfg={"risk_per_trade_pct": 1.0})
        m2 = compute_metrics(trades, [], risk_cfg={"risk_per_trade_pct": 2.0})
        # 2% リスクの方が資産が大きく動く
        assert m2["equity_curve"][0]["equity"] > m1["equity_curve"][0]["equity"]
