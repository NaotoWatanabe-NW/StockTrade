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
    set_signal_message_id, get_signal_by_message_id, signals_pending_notification,
    exists_open_signal_today, expire_stale_signals, signal_attribution,
    _on_signal_trade_change, save_backtest_run,
    realized_pnl_summary,
    save_signal_outcome, get_signal_outcome, signals_needing_outcome_eval,
    score_calibration,
    get_param_overrides, save_param_overrides,
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


class TestRealizedPnlSummary:
    def test_taxes_only_net_profit_after_offsetting_losses(self):
        # 同一通貨グループ内で利益銘柄と損失銘柄を損益通算してから課税する
        rows = [
            {"code": "7203", "realized": 30_000},   # 利益
            {"code": "6758", "realized": -10_000},  # 損失
        ]
        jp = next(s for s in realized_pnl_summary(rows, 0.20315) if s["currency"] == "JPY")
        assert jp["realized"] == 20_000             # 30,000 - 10,000（損益通算後）
        assert jp["tax"] == pytest.approx(20_000 * 0.20315)
        assert jp["realized_after_tax"] == pytest.approx(20_000 - 20_000 * 0.20315)

    def test_separates_jp_and_us_currency_groups(self):
        rows = [
            {"code": "7203", "realized": 10_000},   # 日本株
            {"code": "AAPL", "realized": 200},      # 米国株
        ]
        summary = {s["currency"]: s for s in realized_pnl_summary(rows, 0.20315)}
        assert summary["JPY"]["realized"] == 10_000
        assert summary["USD"]["realized"] == 200
        assert summary["USD"]["tax"] == pytest.approx(200 * 0.20315)

    def test_net_loss_group_is_not_taxed(self):
        rows = [{"code": "7203", "realized": -5_000}]
        jp = next(s for s in realized_pnl_summary(rows, 0.20315) if s["currency"] == "JPY")
        assert jp["tax"] == 0
        assert jp["realized_after_tax"] == -5_000


class TestSignalOutcomes:
    def _buy_signal(self, conn, code="7203", score=50.0):
        return save_signal(
            conn, code=code, side="BUY", score=score,
            entry_price=1000.0, stop_price=950.0, target_price=1100.0,
            entry_kind="LIMIT",
        )

    def _outcome(self, outcome="TARGET", realized_r=2.0, **kw):
        base = {
            "horizon_days": 20, "entry_filled": True, "entry_fill_date": "2026-01-06",
            "outcome": outcome, "hit_target": outcome == "TARGET",
            "hit_stop": outcome == "STOP", "days_to_resolve": 3,
            "mfe_r": 2.1, "mae_r": -0.4, "close_at_horizon": None,
            "realized_r": realized_r, "eval_through": "2026-01-20",
        }
        base.update(kw)
        return base

    def test_save_and_get_round_trip_converts_bools_to_int(self, conn):
        sig = self._buy_signal(conn)
        save_signal_outcome(conn, sig["id"], self._outcome())
        got = get_signal_outcome(conn, sig["id"])
        assert got["outcome"] == "TARGET"
        assert got["entry_filled"] == 1 and got["hit_target"] == 1
        assert got["realized_r"] == 2.0

    def test_save_is_idempotent_upsert(self, conn):
        sig = self._buy_signal(conn)
        save_signal_outcome(conn, sig["id"], self._outcome(outcome="PENDING", realized_r=None))
        save_signal_outcome(conn, sig["id"], self._outcome(outcome="TARGET", realized_r=2.0))
        got = get_signal_outcome(conn, sig["id"])
        assert got["outcome"] == "TARGET"        # 再評価で上書きされる
        rows = conn.execute("SELECT COUNT(*) c FROM signal_outcomes").fetchone()
        assert rows["c"] == 1                     # 1シグナル1行

    def test_pending_signal_needs_reevaluation_but_resolved_does_not(self, conn):
        resolved = self._buy_signal(conn, code="7203")
        pending = self._buy_signal(conn, code="6758")
        unscored = self._buy_signal(conn, code="9984")
        save_signal_outcome(conn, resolved["id"], self._outcome(outcome="TARGET"))
        save_signal_outcome(conn, pending["id"], self._outcome(outcome="PENDING", realized_r=None))
        # unscored は結果行なし
        codes = {s["code"] for s in signals_needing_outcome_eval(conn)}
        assert codes == {"6758", "9984"}         # 確定済み 7203 は除外

    def test_sell_signals_are_not_listed_for_evaluation(self, conn):
        save_signal(conn, code="7203", side="SELL", entry_price=1000, stop_price=1050)
        assert signals_needing_outcome_eval(conn) == []

    def test_calibration_separates_by_score_bucket(self, conn):
        # 低スコア(20-40)は損切、高スコア(60-80)は利確に決着させる
        low = self._buy_signal(conn, code="1111", score=30.0)
        high = self._buy_signal(conn, code="2222", score=70.0)
        save_signal_outcome(conn, low["id"], self._outcome(outcome="STOP", realized_r=-1.0))
        save_signal_outcome(conn, high["id"], self._outcome(outcome="TARGET", realized_r=2.0))

        buckets = {(b["score_lo"], b["score_hi"]): b for b in score_calibration(conn)}
        lo_b = buckets[(20, 40)]
        hi_b = buckets[(60, 80)]
        assert lo_b["n_entered"] == 1 and lo_b["win_rate"] == 0.0 and lo_b["avg_r"] == -1.0
        assert hi_b["n_entered"] == 1 and hi_b["win_rate"] == 1.0 and hi_b["avg_r"] == 2.0
        assert hi_b["n_target"] == 1 and lo_b["n_stop"] == 1

    def test_calibration_excludes_no_entry_from_entered_stats(self, conn):
        s1 = self._buy_signal(conn, code="1111", score=50.0)
        s2 = self._buy_signal(conn, code="2222", score=55.0)
        save_signal_outcome(conn, s1["id"], self._outcome(outcome="TARGET", realized_r=2.0))
        save_signal_outcome(conn, s2["id"], self._outcome(
            outcome="NO_ENTRY", realized_r=None, entry_filled=False))
        b = next(b for b in score_calibration(conn) if (b["score_lo"], b["score_hi"]) == (40, 60))
        assert b["n_signals"] == 2          # 両方カウント
        assert b["n_entered"] == 1          # 約定は1件のみ
        assert b["entry_rate"] == 0.5
        assert b["win_rate"] == 1.0         # 約定分（TARGET）のみで算出


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

    def test_message_id_round_trip(self, conn):
        s = self._buy_signal(conn)
        assert get_signal_by_message_id(conn, "m-1") is None
        set_signal_message_id(conn, s["id"], "m-1")
        found = get_signal_by_message_id(conn, "m-1")
        assert found is not None and found["id"] == s["id"]

    def test_pending_notification_excludes_already_notified(self, conn):
        a = self._buy_signal(conn)
        b = self._buy_signal(conn)
        set_signal_message_id(conn, b["id"], "m-b")   # 通知済みは対象外
        ids = {p["id"] for p in signals_pending_notification(conn)}
        assert a["id"] in ids
        assert b["id"] not in ids

    def test_exists_open_signal_today_dedup(self, conn):
        assert exists_open_signal_today(conn, "7011", "BUY") is False
        self._buy_signal(conn)
        assert exists_open_signal_today(conn, "7011", "BUY") is True
        assert exists_open_signal_today(conn, "7011", "SELL") is False

    def test_signal_without_trades_reports_zero_fill_aggregates(self, conn):
        s = self._buy_signal(conn)
        sig = get_signal(conn, s["id"])
        assert sig["filled_shares"] == 0
        assert sig["sold_shares"] == 0
        assert sig["remaining_shares"] == 0
        assert sig["avg_fill_price"] is None
        assert sig["position_value"] is None

    def test_fill_aggregates_weighted_average_and_position_value(self, conn):
        s = self._buy_signal(conn)
        # 2回に分けて買い：(1000×100 + 1020×100)/200 = 平均1010、残200株
        add_trade(conn, code="7011", side="BUY", shares=100, price=1000,
                  traded_at="2026-06-01", signal_id=s["id"])
        add_trade(conn, code="7011", side="BUY", shares=100, price=1020,
                  traded_at="2026-06-02", signal_id=s["id"])
        sig = get_signal(conn, s["id"])
        assert sig["filled_shares"] == 200
        assert sig["remaining_shares"] == 200
        assert sig["avg_fill_price"] == pytest.approx(1010.0)
        assert sig["position_value"] == pytest.approx(200 * 1010.0)

    def test_partial_close_reduces_remaining_shares(self, conn):
        s = self._buy_signal(conn)
        add_trade(conn, code="7011", side="BUY", shares=100, price=1000,
                  traded_at="2026-06-01", signal_id=s["id"])
        add_trade(conn, code="7011", side="SELL", shares=40, price=1100,
                  traded_at="2026-06-10", signal_id=s["id"])
        sig = get_signal(conn, s["id"])
        assert sig["filled_shares"] == 100
        assert sig["sold_shares"] == 40
        assert sig["remaining_shares"] == 60
        assert sig["avg_sell_price"] == pytest.approx(1100.0)

    def test_list_signals_aggregates_only_own_linked_trades(self, conn):
        a = self._buy_signal(conn)
        b = self._buy_signal(conn)
        add_trade(conn, code="7011", side="BUY", shares=100, price=1000,
                  traded_at="2026-06-01", signal_id=a["id"])
        # 別シグナルbには紐付けない単独取引（signal_id=None）も混在させる
        add_trade(conn, code="7011", side="BUY", shares=300, price=1000,
                  traded_at="2026-06-01")
        by_id = {s["id"]: s for s in list_signals(conn)}
        assert by_id[a["id"]]["filled_shares"] == 100
        assert by_id[b["id"]]["filled_shares"] == 0

    def test_list_trades_filters_by_signal_id(self, conn):
        s = self._buy_signal(conn)
        add_trade(conn, code="7011", side="BUY", shares=100, price=1000,
                  traded_at="2026-06-01", signal_id=s["id"])
        add_trade(conn, code="7011", side="BUY", shares=200, price=1000,
                  traded_at="2026-06-01")  # 単独注文（紐付けなし）
        linked = list_trades(conn, signal_id=s["id"])
        assert len(linked) == 1
        assert linked[0]["shares"] == 100
        assert linked[0]["signal_id"] == s["id"]

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


class TestParamOverrides:
    def test_empty_when_unset(self, conn):
        assert get_param_overrides(conn) == {}

    def test_save_and_get_round_trip(self, conn):
        save_param_overrides(conn, {"breakout_lookback": 40, "trail_atr_mult": 2.5})
        got = get_param_overrides(conn)
        assert got == {"breakout_lookback": 40, "trail_atr_mult": 2.5}

    def test_save_replaces_previous(self, conn):
        save_param_overrides(conn, {"breakout_lookback": 40})
        save_param_overrides(conn, {"trail_atr_mult": 2.0})
        assert get_param_overrides(conn) == {"trail_atr_mult": 2.0}
