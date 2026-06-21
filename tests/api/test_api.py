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
