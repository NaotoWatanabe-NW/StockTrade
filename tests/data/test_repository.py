"""data.repository（holdings/trades のCRUDと損益集計）のテスト

各テストはインメモリSQLite（:memory:）で独立に動かす。実DBファイルには触れない。
"""

import pytest

from data.db import get_connection
from data.repository import (
    list_holdings, get_holding, upsert_holding, delete_holding,
    list_trades, add_trade, delete_trade, realized_pnl,
    sync_holding_from_trades,
    save_signal, get_signal, list_signals, update_signal_status,
    exists_open_signal_today, expire_stale_signals, signal_attribution,
    _on_signal_trade_change, save_backtest_run,
)


@pytest.fixture
def conn():
    c = get_connection(":memory:")
    yield c
    c.close()


class TestHoldings:
    def test_upsert_inserts_new_holding(self, conn):
        h = upsert_holding(conn, code="7203", name="トヨタ", avg_price=2700, shares=100)
        assert h["code"] == "7203" and h["shares"] == 100
        assert h["long_term"] is False

    def test_upsert_updates_existing_holding_by_code(self, conn):
        upsert_holding(conn, code="7203", name="トヨタ", avg_price=2700, shares=100)
        upsert_holding(conn, code="7203", name="トヨタ自動車", avg_price=2800, shares=200)
        assert len(list_holdings(conn)) == 1          # 重複せず1件
        h = get_holding(conn, "7203")
        assert h["shares"] == 200 and h["name"] == "トヨタ自動車"

    def test_long_term_flag_is_persisted_as_bool(self, conn):
        upsert_holding(conn, code="2914", name="JT", long_term=True)
        assert get_holding(conn, "2914")["long_term"] is True

    def test_holding_allows_missing_avg_price(self, conn):
        # 株式分割取得などで建値が無いケース
        h = upsert_holding(conn, code="Q", name="Q", shares=1)
        assert h["avg_price"] is None

    def test_delete_holding_removes_row(self, conn):
        upsert_holding(conn, code="7203", name="トヨタ")
        assert delete_holding(conn, "7203") is True
        assert get_holding(conn, "7203") is None

    def test_delete_missing_holding_returns_false(self, conn):
        assert delete_holding(conn, "0000") is False

    def test_list_holdings_is_ordered_by_code(self, conn):
        upsert_holding(conn, code="9984")
        upsert_holding(conn, code="7203")
        assert [h["code"] for h in list_holdings(conn)] == ["7203", "9984"]


class TestTrades:
    def test_add_trade_returns_persisted_row(self, conn):
        t = add_trade(conn, code="7203", side="BUY", shares=100, price=2700,
                      traded_at="2026-06-01", name="トヨタ")
        assert t["id"] > 0 and t["side"] == "BUY" and t["price"] == 2700

    def test_add_trade_rejects_invalid_side(self, conn):
        with pytest.raises(ValueError):
            add_trade(conn, code="7203", side="HOLD", shares=1, price=100, traded_at="2026-06-01")

    def test_add_trade_rejects_non_positive_shares(self, conn):
        with pytest.raises(ValueError):
            add_trade(conn, code="7203", side="BUY", shares=0, price=100, traded_at="2026-06-01")

    def test_list_trades_filters_by_code(self, conn):
        add_trade(conn, code="7203", side="BUY", shares=100, price=2700, traded_at="2026-06-01")
        add_trade(conn, code="6758", side="BUY", shares=100, price=3800, traded_at="2026-06-02")
        assert [t["code"] for t in list_trades(conn, code="7203")] == ["7203"]

    def test_delete_trade_removes_row(self, conn):
        t = add_trade(conn, code="7203", side="BUY", shares=100, price=2700, traded_at="2026-06-01")
        assert delete_trade(conn, t["id"]) is True
        assert list_trades(conn) == []


class TestSyncHoldingFromTrades:
    def test_buy_trade_updates_avg_price_and_shares(self, conn):
        upsert_holding(conn, code="7203", name="トヨタ", avg_price=2700, shares=100)
        add_trade(conn, code="7203", side="BUY", shares=100, price=2700, traded_at="2026-01-01")
        add_trade(conn, code="7203", side="BUY", shares=100, price=2900, traded_at="2026-02-01")
        sync_holding_from_trades(conn, "7203")
        h = get_holding(conn, "7203")
        assert h["avg_price"] == 2800.0   # (2700+2900)/2
        assert h["shares"] == 200

    def test_sell_trade_reduces_shares(self, conn):
        upsert_holding(conn, code="7203", name="トヨタ", avg_price=2700, shares=200)
        add_trade(conn, code="7203", side="BUY", shares=200, price=2700, traded_at="2026-01-01")
        add_trade(conn, code="7203", side="SELL", shares=50, price=3000, traded_at="2026-02-01")
        sync_holding_from_trades(conn, "7203")
        h = get_holding(conn, "7203")
        assert h["shares"] == 150
        assert h["avg_price"] == 2700.0   # avg_price は BUY のみで計算

    def test_skips_when_holding_not_registered(self, conn):
        # holdingsに存在しない銘柄はスキップ（例外を出さない）
        add_trade(conn, code="XXXX", side="BUY", shares=10, price=100, traded_at="2026-01-01")
        sync_holding_from_trades(conn, "XXXX")  # raises しない
        assert get_holding(conn, "XXXX") is None

    def test_no_trades_leaves_holding_unchanged(self, conn):
        upsert_holding(conn, code="7203", avg_price=2700, shares=100)
        sync_holding_from_trades(conn, "7203")  # 約定記録ゼロ → 何もしない
        h = get_holding(conn, "7203")
        assert h["avg_price"] == 2700 and h["shares"] == 100


class TestRealizedPnl:
    def test_realized_uses_average_cost_on_sold_shares(self, conn):
        # 100株@1000 と 100株@1200 で取得（平均1100）、100株を1500で売却
        add_trade(conn, code="X", side="BUY", shares=100, price=1000, traded_at="2026-01-01")
        add_trade(conn, code="X", side="BUY", shares=100, price=1200, traded_at="2026-02-01")
        add_trade(conn, code="X", side="SELL", shares=100, price=1500, traded_at="2026-03-01")
        pnl = realized_pnl(conn)[0]
        assert pnl["avg_cost"] == 1100
        assert pnl["remaining_shares"] == 100
        # 実現損益 = 1500*100 - 1100*100 - 手数料0 = 40,000
        assert pnl["realized"] == 40_000

    def test_realized_subtracts_fees(self, conn):
        add_trade(conn, code="X", side="BUY", shares=100, price=1000, traded_at="2026-01-01", fee=500)
        add_trade(conn, code="X", side="SELL", shares=100, price=1200, traded_at="2026-03-01", fee=500)
        pnl = realized_pnl(conn)[0]
        # 1200*100 - 1000*100 - (500+500) = 19,000
        assert pnl["realized"] == 19_000

    def test_realized_is_zero_without_sells(self, conn):
        add_trade(conn, code="X", side="BUY", shares=100, price=1000, traded_at="2026-01-01")
        pnl = realized_pnl(conn)[0]
        assert pnl["realized"] == 0
        assert pnl["remaining_shares"] == 100


class TestBacktestRunRepository:
    def test_save_and_get_returns_same_run(self, conn):
        from data.repository import save_backtest_run, get_backtest_run
        metrics = {
            "total_signals": 50, "filled": 40, "fill_rate": 0.8,
            "closed": 38, "win_rate": 0.6, "avg_r": 0.5,
            "profit_factor": 1.8, "max_drawdown_r": -3.0, "time_stop_rate": 0.1,
            "sharpe_ratio": 1.2, "annual_return_pct": 8.5,
            "equity_curve": [{"date": "2024-01-10", "equity": 1.05}],
        }
        run_id = save_backtest_run(conn, "JP", metrics, {"trail": 2.0})
        row = get_backtest_run(conn, run_id)
        assert row is not None
        assert row["universe"] == "JP"
        assert abs(row["sharpe"] - 1.2) < 0.001
        assert abs(row["annual_return_pct"] - 8.5) < 0.001
        assert row["equity_curve"] is not None  # JSON 文字列で保存されている

    def test_get_nonexistent_run_returns_none(self, conn):
        from data.repository import get_backtest_run
        assert get_backtest_run(conn, 9999) is None

    def test_list_excludes_equity_curve_column(self, conn):
        from data.repository import save_backtest_run, list_backtest_runs
        metrics = {
            "total_signals": 10, "filled": 8, "fill_rate": 0.8,
            "closed": 7, "win_rate": 0.57, "avg_r": 0.3,
            "profit_factor": 1.5, "max_drawdown_r": -1.0, "time_stop_rate": 0.1,
            "sharpe_ratio": 0.9, "annual_return_pct": 5.0,
            "equity_curve": [{"date": "2024-01-10", "equity": 1.05}],
        }
        save_backtest_run(conn, "US", metrics, {})
        runs = list_backtest_runs(conn)
        assert len(runs) >= 1
        # 一覧には equity_curve は含まない（帯域節約）
        assert "equity_curve" not in runs[0]


class TestSignals:
    def _buy_signal(self, conn):
        # entry 1000 / stop 950 → risk 50（1R = 50円）
        return save_signal(
            conn, code="7011", side="BUY", name="三菱重工", market="JP",
            signal_types=["BREAKOUT_HIGH"], score=45.0,
            entry_price=1000.0, stop_price=950.0, target_price=1150.0,
            entry_kind="LIMIT", order_type="IFDOCO",
        )

    def test_save_signal_computes_risk_and_defaults_open(self, conn):
        s = self._buy_signal(conn)
        assert s["risk"] == 50.0
        assert s["status"] == "OPEN"
        assert s["side"] == "BUY"

    def test_save_signal_rejects_invalid_side(self, conn):
        with pytest.raises(ValueError):
            save_signal(conn, code="7011", side="HOLD")

    def test_list_signals_filters_by_status(self, conn):
        self._buy_signal(conn)
        s2 = self._buy_signal(conn)
        update_signal_status(conn, s2["id"], "SKIPPED")
        assert len(list_signals(conn, status="OPEN")) == 1
        assert len(list_signals(conn, status="SKIPPED")) == 1

    def test_linking_buy_trade_marks_signal_taken(self, conn):
        s = self._buy_signal(conn)
        add_trade(conn, code="7011", side="BUY", shares=100, price=1000,
                  traded_at="2026-06-01", signal_id=s["id"])
        assert get_signal(conn, s["id"])["status"] == "TAKEN"

    def test_full_round_trip_computes_realized_r_winner(self, conn):
        s = self._buy_signal(conn)
        # 1000で買い、1100で売り → (1100-1000)/50 = +2.0R
        add_trade(conn, code="7011", side="BUY", shares=100, price=1000,
                  traded_at="2026-06-01", signal_id=s["id"])
        add_trade(conn, code="7011", side="SELL", shares=100, price=1100,
                  traded_at="2026-06-10", signal_id=s["id"])
        sig = get_signal(conn, s["id"])
        assert sig["status"] == "CLOSED"
        assert sig["realized_r"] == pytest.approx(2.0)

    def test_full_round_trip_computes_realized_r_loser(self, conn):
        s = self._buy_signal(conn)
        # 1000で買い、950で売り → (950-1000)/50 = -1.0R
        add_trade(conn, code="7011", side="BUY", shares=100, price=1000,
                  traded_at="2026-06-01", signal_id=s["id"])
        add_trade(conn, code="7011", side="SELL", shares=100, price=950,
                  traded_at="2026-06-10", signal_id=s["id"])
        sig = get_signal(conn, s["id"])
        assert sig["realized_r"] == pytest.approx(-1.0)

    def test_partial_sell_keeps_status_taken(self, conn):
        s = self._buy_signal(conn)
        add_trade(conn, code="7011", side="BUY", shares=100, price=1000,
                  traded_at="2026-06-01", signal_id=s["id"])
        # 半分だけ売却 → まだ建玉が残るので CLOSED にしない
        add_trade(conn, code="7011", side="SELL", shares=50, price=1100,
                  traded_at="2026-06-10", signal_id=s["id"])
        sig = get_signal(conn, s["id"])
        assert sig["status"] == "TAKEN"
        assert sig["realized_r"] is None

    def test_weighted_average_entry_and_exit(self, conn):
        s = self._buy_signal(conn)
        # 2回に分けて買い：(1000×100 + 1020×100)/200 = 1010
        add_trade(conn, code="7011", side="BUY", shares=100, price=1000,
                  traded_at="2026-06-01", signal_id=s["id"])
        add_trade(conn, code="7011", side="BUY", shares=100, price=1020,
                  traded_at="2026-06-02", signal_id=s["id"])
        # 全株売却：1110 → (1110-1010)/50 = +2.0R
        add_trade(conn, code="7011", side="SELL", shares=200, price=1110,
                  traded_at="2026-06-10", signal_id=s["id"])
        sig = get_signal(conn, s["id"])
        assert sig["realized_r"] == pytest.approx(2.0)

    def test_deleting_linked_trade_reverts_status(self, conn):
        s = self._buy_signal(conn)
        t = add_trade(conn, code="7011", side="BUY", shares=100, price=1000,
                      traded_at="2026-06-01", signal_id=s["id"])
        assert get_signal(conn, s["id"])["status"] == "TAKEN"
        delete_trade(conn, t["id"])
        assert get_signal(conn, s["id"])["status"] == "OPEN"

    def test_skipped_status_preserved_when_no_trades(self, conn):
        s = self._buy_signal(conn)
        update_signal_status(conn, s["id"], "SKIPPED")
        # 取引が無い状態で再計算がかかっても SKIPPED を維持する
        _on_signal_trade_change(conn, s["id"])
        assert get_signal(conn, s["id"])["status"] == "SKIPPED"

    def test_exists_open_signal_today_dedup(self, conn):
        assert exists_open_signal_today(conn, "7011", "BUY") is False
        self._buy_signal(conn)
        assert exists_open_signal_today(conn, "7011", "BUY") is True
        assert exists_open_signal_today(conn, "7011", "SELL") is False

    def test_attribution_live_vs_backtest(self, conn):
        # バックテスト期待値を1件保存
        save_backtest_run(conn, "JP", {
            "total_signals": 100, "filled": 80, "fill_rate": 0.8,
            "closed": 75, "win_rate": 0.568, "avg_r": 0.094,
            "profit_factor": 1.25, "max_drawdown_r": -23.7, "time_stop_rate": 0.2,
        }, {})
        # 勝ち1・負け1・見送り1
        win = self._buy_signal(conn)
        add_trade(conn, code="7011", side="BUY", shares=100, price=1000, traded_at="2026-06-01", signal_id=win["id"])
        add_trade(conn, code="7011", side="SELL", shares=100, price=1100, traded_at="2026-06-10", signal_id=win["id"])
        lose = self._buy_signal(conn)
        add_trade(conn, code="7011", side="BUY", shares=100, price=1000, traded_at="2026-06-01", signal_id=lose["id"])
        add_trade(conn, code="7011", side="SELL", shares=100, price=950, traded_at="2026-06-10", signal_id=lose["id"])
        skip = self._buy_signal(conn)
        update_signal_status(conn, skip["id"], "SKIPPED")

        a = signal_attribution(conn)
        assert a["total"] == 3
        assert a["closed"] == 2
        assert a["skipped"] == 1
        assert a["live_win_rate"] == pytest.approx(0.5)      # 1勝1敗
        assert a["live_avg_r"] == pytest.approx(0.5)          # (+2 -1)/2
        assert a["bt_avg_r"] == pytest.approx(0.094)          # バックテスト期待値
        # 終局3件中2件約定 → take_rate = 2/3
        assert a["take_rate"] == pytest.approx(2 / 3)
