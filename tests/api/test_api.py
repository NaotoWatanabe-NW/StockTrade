"""FastAPI エンドポイントの統合テスト

DB依存だけをインメモリSQLite（共有接続）に差し替え、HTTP経由でCRUDを検証する。
"""

import json
from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.main import app


def _seed_backtest_cache(db_path: str, code: str, days: int = 300) -> None:
    """当日で終わる連続日足を price_history に投入する（Webバックテストをオフラインで走らせる用）。"""
    from data.db import get_connection
    conn = get_connection(db_path)
    dates = pd.bdate_range(end=date.today(), periods=days)
    rows = []
    for i, d in enumerate(dates):
        close = 1000 + i * 0.5 + 20 * (((i % 10) - 5) / 5.0)
        rows.append((code, "1d", str(d.date()), close - 4, close + 8, close - 8, close, 100_000))
    conn.executemany(
        "INSERT OR REPLACE INTO price_history "
        "(code, interval, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


@pytest.fixture
def client(tmp_path, monkeypatch):
    # 各テストは独立した一時DBファイルを使う（TestClientは別スレッドで動くため、
    # リクエストごとにそのスレッドで接続を開く実運用と同じ経路を通す）
    monkeypatch.setenv("STOCK_DB_PATH", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


class TestHealth:
    def test_health_returns_ok(self, client):
        assert client.get("/api/health").json() == {"status": "ok"}


class TestHoldingsApi:
    def test_create_then_list_holding(self, client):
        r = client.post("/api/holdings", json={"code": "7203", "name": "トヨタ", "shares": 100})
        assert r.status_code == 200 and r.json()["code"] == "7203"
        listed = client.get("/api/holdings").json()
        assert [h["code"] for h in listed] == ["7203"]

    def test_posting_same_code_updates_in_place(self, client):
        client.post("/api/holdings", json={"code": "7203", "shares": 100})
        client.post("/api/holdings", json={"code": "7203", "shares": 300, "long_term": True})
        listed = client.get("/api/holdings").json()
        assert len(listed) == 1
        assert listed[0]["shares"] == 300 and listed[0]["long_term"] is True

    def test_read_missing_holding_returns_404(self, client):
        assert client.get("/api/holdings/0000").status_code == 404

    def test_delete_holding(self, client):
        client.post("/api/holdings", json={"code": "7203"})
        assert client.delete("/api/holdings/7203").status_code == 200
        assert client.delete("/api/holdings/7203").status_code == 404


class TestTradesApi:
    def test_create_and_list_trade(self, client):
        payload = {"code": "7203", "side": "BUY", "shares": 100, "price": 2700, "traded_at": "2026-06-01"}
        assert client.post("/api/trades", json=payload).status_code == 200
        trades = client.get("/api/trades").json()
        assert len(trades) == 1 and trades[0]["side"] == "BUY"

    def test_invalid_side_returns_400(self, client):
        payload = {"code": "7203", "side": "HOLD", "shares": 100, "price": 2700, "traded_at": "2026-06-01"}
        assert client.post("/api/trades", json=payload).status_code == 400

    def test_non_positive_shares_rejected_by_validation(self, client):
        payload = {"code": "7203", "side": "BUY", "shares": 0, "price": 2700, "traded_at": "2026-06-01"}
        assert client.post("/api/trades", json=payload).status_code == 422

    def test_filter_trades_by_code(self, client):
        client.post("/api/trades", json={"code": "7203", "side": "BUY", "shares": 1, "price": 2700, "traded_at": "2026-06-01"})
        client.post("/api/trades", json={"code": "6758", "side": "BUY", "shares": 1, "price": 3800, "traded_at": "2026-06-01"})
        assert len(client.get("/api/trades", params={"code": "7203"}).json()) == 1

    def test_delete_trade(self, client):
        tid = client.post("/api/trades", json={"code": "7203", "side": "BUY", "shares": 1, "price": 2700, "traded_at": "2026-06-01"}).json()["id"]
        assert client.delete(f"/api/trades/{tid}").status_code == 200
        assert client.delete(f"/api/trades/{tid}").status_code == 404


class TestPnlApi:
    def test_realized_pnl_reflects_trades(self, client):
        client.post("/api/trades", json={"code": "7203", "side": "BUY", "shares": 100, "price": 1000, "traded_at": "2026-01-01"})
        client.post("/api/trades", json={"code": "7203", "side": "SELL", "shares": 100, "price": 1500, "traded_at": "2026-03-01"})
        pnl = client.get("/api/pnl").json()
        assert pnl["rows"][0]["code"] == "7203"
        assert pnl["rows"][0]["realized"] == 50_000   # (1500-1000)*100

    def test_summary_applies_capital_gains_tax_to_profit(self, client):
        from config import TAX_CONFIG

        client.post("/api/trades", json={"code": "7203", "side": "BUY", "shares": 100, "price": 1000, "traded_at": "2026-01-01"})
        client.post("/api/trades", json={"code": "7203", "side": "SELL", "shares": 100, "price": 1500, "traded_at": "2026-03-01"})
        summary = client.get("/api/pnl").json()["summary"]
        jp = next(s for s in summary if s["currency"] == "JPY")
        rate = TAX_CONFIG["capital_gains_rate"]
        assert jp["realized"] == 50_000
        assert jp["tax"] == 50_000 * rate
        assert jp["realized_after_tax"] == 50_000 - 50_000 * rate

    def test_summary_does_not_tax_a_loss(self, client):
        client.post("/api/trades", json={"code": "7203", "side": "BUY", "shares": 100, "price": 1500, "traded_at": "2026-01-01"})
        client.post("/api/trades", json={"code": "7203", "side": "SELL", "shares": 100, "price": 1000, "traded_at": "2026-03-01"})
        summary = client.get("/api/pnl").json()["summary"]
        jp = next(s for s in summary if s["currency"] == "JPY")
        assert jp["realized"] == -50_000
        assert jp["tax"] == 0
        assert jp["realized_after_tax"] == -50_000


class TestBacktestApi:
    def test_empty_returns_empty_list(self, client):
        assert client.get("/api/backtest").json() == []

    def test_save_and_retrieve(self, client, monkeypatch, tmp_path):
        """runner経由ではなくrepository直呼びで保存→API確認"""
        from data.db import get_connection
        from data.repository import save_backtest_run
        db_path = str(tmp_path / "test.db")
        monkeypatch.setenv("STOCK_DB_PATH", db_path)
        conn = get_connection(db_path)
        metrics = {
            "total_signals": 100, "filled": 80, "fill_rate": 0.8,
            "closed": 75, "win_rate": 0.55, "avg_r": 0.12,
            "profit_factor": 1.3, "max_drawdown_r": -5.0, "time_stop_rate": 0.2,
        }
        save_backtest_run(conn, "JP", metrics, {"trail_atr_mult": 2.0})
        conn.close()

        with TestClient(app) as c:
            runs = c.get("/api/backtest").json()
        assert len(runs) == 1
        assert runs[0]["universe"] == "JP"
        assert abs(runs[0]["win_rate"] - 0.55) < 0.001


class TestPortfolioApi:
    def test_heat_returns_valid_structure(self, client):
        r = client.get("/api/portfolio/heat")
        assert r.status_code == 200
        body = r.json()
        assert "heat_pct" in body
        assert "max_positions" in body
        assert body["heat_pct"] >= 0

    def test_heat_increases_with_holdings(self, client):
        r0 = client.get("/api/portfolio/heat").json()
        client.post("/api/holdings", json={"code": "7203", "name": "Toyota", "shares": 100})
        r1 = client.get("/api/portfolio/heat").json()
        assert r1["heat_pct"] > r0["heat_pct"]


class TestBacktestDetailApi:
    def test_get_by_id_returns_equity_curve(self, client, tmp_path, monkeypatch):
        from data.db import get_connection
        from data.repository import save_backtest_run
        db_path = str(tmp_path / "test2.db")
        monkeypatch.setenv("STOCK_DB_PATH", db_path)
        conn = get_connection(db_path)
        metrics = {
            "total_signals": 20, "filled": 16, "fill_rate": 0.8,
            "closed": 15, "win_rate": 0.6, "avg_r": 0.4,
            "profit_factor": 1.5, "max_drawdown_r": -2.0, "time_stop_rate": 0.1,
            "sharpe_ratio": 1.1, "annual_return_pct": 6.0,
            "equity_curve": [{"date": "2024-06-01", "equity": 1.06}],
        }
        run_id = save_backtest_run(conn, "JP", metrics, {})
        conn.close()

        with TestClient(app) as c:
            r = c.get(f"/api/backtest/{run_id}")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == run_id
        assert body["equity_curve"] is not None
        assert body["sharpe"] is not None

    def test_get_nonexistent_returns_404(self, client):
        assert client.get("/api/backtest/9999").status_code == 404


class TestSignalsApi:
    def _make_signal(self, client):
        """repository経由でシグナルを1件作る（APIに作成口は無いため直接）"""
        from data.db import get_connection
        from data.repository import save_signal
        import os
        conn = get_connection(os.environ["STOCK_DB_PATH"])
        s = save_signal(conn, code="7011", side="BUY", name="三菱重工", market="JP",
                        signal_types=["BREAKOUT_HIGH"], score=42.0,
                        entry_price=1000.0, stop_price=950.0, target_price=1150.0,
                        entry_kind="LIMIT", order_type="IFDOCO")
        conn.close()
        return s["id"]

    def test_list_signals_empty(self, client):
        assert client.get("/api/signals").json() == []

    def test_get_signal_and_filter_by_status(self, client):
        sid = self._make_signal(client)
        got = client.get(f"/api/signals/{sid}").json()
        assert got["code"] == "7011" and got["status"] == "OPEN"
        assert len(client.get("/api/signals", params={"status": "OPEN"}).json()) == 1
        assert client.get("/api/signals", params={"status": "CLOSED"}).json() == []

    def test_get_missing_signal_404(self, client):
        assert client.get("/api/signals/9999").status_code == 404

    def test_lookup_signal_by_discord_message_id(self, client):
        from data.db import get_connection
        from data.repository import set_signal_message_id
        import os
        sid = self._make_signal(client)
        conn = get_connection(os.environ["STOCK_DB_PATH"])
        set_signal_message_id(conn, sid, "disc-42")
        conn.close()
        got = client.get("/api/signals/by-message/disc-42")
        assert got.status_code == 200 and got.json()["id"] == sid
        assert client.get("/api/signals/by-message/nope").status_code == 404

    def test_update_status_to_skipped(self, client):
        sid = self._make_signal(client)
        r = client.post(f"/api/signals/{sid}/status", json={"status": "SKIPPED"})
        assert r.status_code == 200 and r.json()["status"] == "SKIPPED"

    def test_invalid_status_400(self, client):
        sid = self._make_signal(client)
        assert client.post(f"/api/signals/{sid}/status", json={"status": "BOGUS"}).status_code == 400

    def test_trade_linked_to_signal_marks_taken_and_computes_r(self, client):
        sid = self._make_signal(client)
        # BUY → TAKEN
        client.post("/api/trades", json={
            "code": "7011", "side": "BUY", "shares": 100, "price": 1000,
            "traded_at": "2026-06-01", "signal_id": sid})
        assert client.get(f"/api/signals/{sid}").json()["status"] == "TAKEN"
        # SELL（全株決済）→ CLOSED, realized_r = (1100-1000)/50 = +2.0
        client.post("/api/trades", json={
            "code": "7011", "side": "SELL", "shares": 100, "price": 1100,
            "traded_at": "2026-06-10", "signal_id": sid})
        sig = client.get(f"/api/signals/{sid}").json()
        assert sig["status"] == "CLOSED"
        assert abs(sig["realized_r"] - 2.0) < 1e-6

    def test_fill_endpoint_marks_taken_and_reports_position(self, client):
        sid = self._make_signal(client)
        r = client.post(f"/api/signals/{sid}/fill",
                        json={"shares": 100, "price": 1000, "traded_at": "2026-06-01"})
        assert r.status_code == 200
        sig = r.json()
        assert sig["status"] == "TAKEN"
        assert sig["filled_shares"] == 100
        assert sig["remaining_shares"] == 100
        assert abs(sig["avg_fill_price"] - 1000) < 1e-6

    def test_close_endpoint_marks_closed_and_computes_r(self, client):
        sid = self._make_signal(client)
        client.post(f"/api/signals/{sid}/fill",
                    json={"shares": 100, "price": 1000, "traded_at": "2026-06-01"})
        r = client.post(f"/api/signals/{sid}/close",
                        json={"shares": 100, "price": 1100, "traded_at": "2026-06-10"})
        sig = r.json()
        assert sig["status"] == "CLOSED"
        assert abs(sig["realized_r"] - 2.0) < 1e-6   # (1100-1000)/50
        assert sig["remaining_shares"] == 0

    def test_fill_on_missing_signal_returns_404(self, client):
        assert client.post("/api/signals/9999/fill",
                           json={"shares": 100, "price": 1000, "traded_at": "2026-06-01"}
                           ).status_code == 404

    def test_fill_rejects_non_positive_shares(self, client):
        sid = self._make_signal(client)
        assert client.post(f"/api/signals/{sid}/fill",
                           json={"shares": 0, "price": 1000, "traded_at": "2026-06-01"}
                           ).status_code == 422

    def test_signal_can_be_marked_expired(self, client):
        sid = self._make_signal(client)
        r = client.post(f"/api/signals/{sid}/status", json={"status": "EXPIRED"})
        assert r.status_code == 200 and r.json()["status"] == "EXPIRED"

    def test_signal_trades_endpoint_lists_only_linked_fills(self, client):
        sid = self._make_signal(client)
        client.post(f"/api/signals/{sid}/fill",
                    json={"shares": 100, "price": 1000, "traded_at": "2026-06-01"})
        # 単独注文（紐付けなし）は混ざらない
        client.post("/api/trades", json={"code": "7011", "side": "BUY", "shares": 50,
                    "price": 1000, "traded_at": "2026-06-02"})
        linked = client.get(f"/api/signals/{sid}/trades").json()
        assert len(linked) == 1 and linked[0]["signal_id"] == sid

    def test_signal_fill_flows_into_trades_and_pnl(self, client):
        # 棲み分け: シグナル経由の約定も取引一覧・損益に反映される
        sid = self._make_signal(client)
        client.post(f"/api/signals/{sid}/fill",
                    json={"shares": 100, "price": 1000, "traded_at": "2026-06-01"})
        client.post(f"/api/signals/{sid}/close",
                    json={"shares": 100, "price": 1100, "traded_at": "2026-06-10"})
        all_trades = client.get("/api/trades").json()
        assert {t["side"] for t in all_trades} == {"BUY", "SELL"}
        assert all(t["signal_id"] == sid for t in all_trades)
        pnl = client.get("/api/pnl").json()
        row = next(r for r in pnl["rows"] if r["code"] == "7011")
        assert row["realized"] == pytest.approx((1100 - 1000) * 100)

    def test_attribution_reflects_live_and_backtest(self, client):
        from data.db import get_connection
        from data.repository import save_backtest_run
        import os
        conn = get_connection(os.environ["STOCK_DB_PATH"])
        save_backtest_run(conn, "JP", {
            "total_signals": 100, "filled": 80, "fill_rate": 0.8, "closed": 75,
            "win_rate": 0.568, "avg_r": 0.094, "profit_factor": 1.25,
            "max_drawdown_r": -23.7, "time_stop_rate": 0.2}, {})
        conn.close()

        sid = self._make_signal(client)
        client.post("/api/trades", json={"code": "7011", "side": "BUY", "shares": 100,
                    "price": 1000, "traded_at": "2026-06-01", "signal_id": sid})
        client.post("/api/trades", json={"code": "7011", "side": "SELL", "shares": 100,
                    "price": 1100, "traded_at": "2026-06-10", "signal_id": sid})

        a = client.get("/api/signals/attribution").json()
        assert a["total"] == 1
        assert a["closed"] == 1
        assert abs(a["live_avg_r"] - 2.0) < 1e-6
        assert abs(a["bt_avg_r"] - 0.094) < 1e-6

    def test_calibration_endpoint_aggregates_outcomes_by_score(self, client):
        from data.db import get_connection
        from data.repository import save_signal, save_signal_outcome
        import os
        conn = get_connection(os.environ["STOCK_DB_PATH"])
        sig = save_signal(conn, code="7011", side="BUY", score=70.0,
                          entry_price=1000.0, stop_price=950.0, target_price=1100.0,
                          entry_kind="LIMIT")
        save_signal_outcome(conn, sig["id"], {
            "horizon_days": 20, "entry_filled": True, "entry_fill_date": "2026-01-06",
            "outcome": "TARGET", "hit_target": True, "hit_stop": False,
            "days_to_resolve": 3, "mfe_r": 2.1, "mae_r": -0.3,
            "close_at_horizon": None, "realized_r": 2.0, "eval_through": "2026-01-20"})
        conn.close()

        buckets = client.get("/api/signals/calibration").json()
        # ルートが /{signal_id} に飲まれず正しく集計される
        hi = next(b for b in buckets if b["score_lo"] == 60 and b["score_hi"] == 80)
        assert hi["n_entered"] == 1
        assert hi["win_rate"] == 1.0
        assert hi["avg_r"] == 2.0


class TestLiveExpiryApi:
    def _open_signal(self, age_days: int):
        """指定日数前に生成された OPEN な BUY シグナルを作る。"""
        from data.db import get_connection
        from data.repository import save_signal
        import os
        conn = get_connection(os.environ["STOCK_DB_PATH"])
        s = save_signal(conn, code="7011", side="BUY", entry_price=1000, stop_price=950)
        conn.execute(
            "UPDATE signals SET generated_at = datetime('now', ?) WHERE id = ?",
            (f"-{age_days} days", s["id"]),
        )
        conn.commit()
        conn.close()
        return s["id"]

    def test_listing_auto_expires_stale_open_signal(self, client):
        sid = self._open_signal(age_days=40)   # 有効期限(15営業日≒22暦日)を超過
        listed = client.get("/api/signals").json()
        assert next(x for x in listed if x["id"] == sid)["status"] == "EXPIRED"

    def test_recent_open_signal_is_not_expired(self, client):
        sid = self._open_signal(age_days=1)
        listed = client.get("/api/signals").json()
        assert next(x for x in listed if x["id"] == sid)["status"] == "OPEN"

    def test_attribution_take_rate_reflects_auto_expiry(self, client):
        # 古いOPEN1件のみ → 期限切れで終局1件・約定0 → take_rate=0
        self._open_signal(age_days=40)
        a = client.get("/api/signals/attribution").json()
        assert a["expired"] == 1
        assert a["take_rate"] == 0


class TestNameLookupApi:
    def test_returns_known_name_from_db_without_network(self, client):
        client.post("/api/holdings", json={"code": "7203", "name": "トヨタ自動車"})
        r = client.get("/api/lookup/name", params={"code": "7203"}).json()
        assert r["name"] == "トヨタ自動車"
        assert r["source"] == "db"

    def test_empty_code_returns_null_name(self, client):
        r = client.get("/api/lookup/name", params={"code": "  "}).json()
        assert r["name"] is None and r["source"] is None

    def test_falls_back_to_yfinance_for_unknown_code(self, client, monkeypatch):
        # 未登録コードは外部取得（yfinance）にフォールバックする。ネットワークは差し替え。
        from core.data_client import StockDataClient
        monkeypatch.setattr(
            StockDataClient, "get_info",
            lambda self, code, market=None: {"code": code, "name": "Apple Inc."},
        )
        r = client.get("/api/lookup/name", params={"code": "AAPL"}).json()
        assert r["name"] == "Apple Inc."
        assert r["source"] == "yfinance"


class TestSizingApi:
    def _open_buy(self, code="7011", entry=1000.0, stop=950.0):
        from data.db import get_connection
        from data.repository import save_signal
        import os
        conn = get_connection(os.environ["STOCK_DB_PATH"])
        s = save_signal(conn, code=code, side="BUY", name="三菱重工", market="JP",
                        entry_price=entry, stop_price=stop, target_price=1150.0,
                        entry_kind="LIMIT", score=42.0)
        conn.close()
        return s["id"]

    def test_recommends_shares_from_account_and_risk(self, client):
        sid = self._open_buy()
        # 既定 account 1,000,000 × risk 1% = 許容10,000、損切り幅50 → 200株（ロット100）
        r = client.get("/api/portfolio/suggestions").json()
        assert r["account_size"] == 1_000_000
        sug = next(s for s in r["suggestions"] if s["signal_id"] == sid)
        assert sug["suggested_shares"] == 200
        assert sug["lot_size"] == 100
        assert sug["investment"] == 200 * 1000
        assert sug["risk_amount"] == 200 * 50

    def test_excludes_sell_and_non_open_signals(self, client):
        from data.db import get_connection
        from data.repository import save_signal, update_signal_status
        import os
        conn = get_connection(os.environ["STOCK_DB_PATH"])
        save_signal(conn, code="7011", side="SELL", entry_price=1000, stop_price=1050)
        taken = save_signal(conn, code="6758", side="BUY", entry_price=2000, stop_price=1900)
        update_signal_status(conn, taken["id"], "SKIPPED")
        conn.close()
        assert client.get("/api/portfolio/suggestions").json()["suggestions"] == []

    def test_account_size_override_scales_recommendation(self, client):
        sid = self._open_buy()
        client.put("/api/settings", json={"values": {"account_size": 2_000_000}})
        r = client.get("/api/portfolio/suggestions").json()
        sug = next(s for s in r["suggestions"] if s["signal_id"] == sid)
        assert sug["suggested_shares"] == 400   # 口座2倍 → 株数2倍

    def test_remaining_slots_and_heat_reflect_holdings(self, client):
        client.post("/api/holdings", json={"code": "7203", "shares": 100})
        r = client.get("/api/portfolio/suggestions").json()
        assert r["open_positions"] == 1
        assert r["remaining_slots"] == r["max_positions"] - 1
        assert r["heat_pct"] == r["risk_per_trade_pct"]


class TestSettingsApi:
    def test_get_returns_all_tunable_params_unoverridden(self, client):
        items = client.get("/api/settings").json()
        params = {i["param"] for i in items}
        assert "breakout_lookback" in params
        assert "account_size" in params
        assert all(i["overridden"] is False for i in items)
        # value はデフォルトと一致
        assert all(i["value"] == i["default"] for i in items)

    def test_put_overrides_value_and_marks_overridden(self, client):
        r = client.put("/api/settings", json={"values": {"breakout_lookback": 40}})
        assert r.status_code == 200
        item = next(i for i in r.json() if i["param"] == "breakout_lookback")
        assert item["value"] == 40
        assert item["overridden"] is True

    def test_put_preserves_integer_type(self, client):
        # ma_short は整数パラメータ。float 化して窓幅を壊さないこと
        r = client.put("/api/settings", json={"values": {"ma_short": 8}})
        item = next(i for i in r.json() if i["param"] == "ma_short")
        assert item["value"] == 8
        assert isinstance(item["value"], int)

    def test_put_rejects_unknown_param(self, client):
        r = client.put("/api/settings", json={"values": {"bogus": 1}})
        assert r.status_code == 400

    def test_delete_resets_to_default(self, client):
        client.put("/api/settings", json={"values": {"breakout_lookback": 40}})
        r = client.delete("/api/settings/breakout_lookback")
        item = next(i for i in r.json() if i["param"] == "breakout_lookback")
        assert item["overridden"] is False
        assert item["value"] == item["default"]

    def test_override_flows_into_backtest_defaults(self, client):
        # 実行時マージ: 上書き保存後、バックテストの defaults（有効値）が変わる
        client.put("/api/settings", json={"values": {"breakout_lookback": 40}})
        defaults = client.get("/api/backtest/defaults").json()
        assert defaults["breakout_lookback"] == 40


class TestBacktestRunApi:
    def _seed_jp(self, monkeypatch):
        """JPユニバースを1銘柄に絞り、キャッシュを当日まで埋めてオフライン実行可能にする。"""
        import os
        import config
        monkeypatch.setitem(config.BACKTEST_CONFIG, "history", "1y")
        monkeypatch.setattr(config, "SCREENING_UNIVERSE_JP", ["9999"])
        _seed_backtest_cache(os.environ["STOCK_DB_PATH"], "9999", days=300)

    def test_defaults_returns_tunable_params(self, client):
        d = client.get("/api/backtest/defaults").json()
        assert "atr_stop_mult" in d
        assert "trail_atr_mult" in d
        assert "min_abs_score" in d

    def test_run_executes_in_background_and_completes(self, client, monkeypatch):
        self._seed_jp(monkeypatch)
        r = client.post("/api/backtest/run", json={
            "universe": "JP", "regime": False,
            "params": {"min_abs_score": 40, "trail_atr_mult": 3.0}})
        assert r.status_code == 202
        body = r.json()
        assert body["status"] == "running"   # 応答はジョブ作成直後（実行前）の状態
        run_id = body["id"]

        # TestClient はバックグラウンドタスクを応答後に同期実行するため、ここでは完了済み
        got = client.get(f"/api/backtest/{run_id}").json()
        assert got["status"] == "done"
        assert got["universe"] == "JP"
        params = json.loads(got["params"])
        assert params["backtest_cfg"]["min_abs_score"] == 40
        assert params["exit_cfg"]["trail_atr_mult"] == 3.0

    def test_run_rejects_unknown_param_with_400(self, client):
        r = client.post("/api/backtest/run", json={
            "universe": "JP", "params": {"not_a_param": 1}})
        assert r.status_code == 400

    def test_completed_run_appears_in_list(self, client, monkeypatch):
        self._seed_jp(monkeypatch)
        client.post("/api/backtest/run", json={"universe": "JP", "regime": False})
        runs = client.get("/api/backtest").json()
        assert len(runs) == 1
        assert runs[0]["status"] == "done"
