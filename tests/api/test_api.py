"""FastAPI エンドポイントの統合テスト

DB依存だけをインメモリSQLite（共有接続）に差し替え、HTTP経由でCRUDを検証する。
"""

import pytest
from fastapi.testclient import TestClient

from api.main import app


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
        client.post("/api/trades", json={"code": "X", "side": "BUY", "shares": 100, "price": 1000, "traded_at": "2026-01-01"})
        client.post("/api/trades", json={"code": "X", "side": "SELL", "shares": 100, "price": 1500, "traded_at": "2026-03-01"})
        pnl = client.get("/api/pnl").json()
        assert pnl[0]["code"] == "X"
        assert pnl[0]["realized"] == 50_000   # (1500-1000)*100


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
