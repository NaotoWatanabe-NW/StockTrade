"""data.repository（holdings/trades のCRUDと損益集計）のテスト

各テストはインメモリSQLite（:memory:）で独立に動かす。実DBファイルには触れない。
"""

import pytest

from data.db import get_connection
from data.repository import (
    list_holdings, get_holding, upsert_holding, delete_holding,
    list_trades, add_trade, delete_trade, realized_pnl,
    sync_holding_from_trades,
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
