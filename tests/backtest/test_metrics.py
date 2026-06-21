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
