"""screener.engine の統合テスト

データ取得（yfinance）だけをフェイククライアントに差し替え、指標計算→
シグナル判定→価格プラン→注文組み立て→保有損益までの一連の流れを検証する。
yfinanceは実I/Oかつ遅延・ブロックの懸念があるため、ここだけ最小限の
フェイクに置き換える（指標・注文ロジックは本物を通す）。
"""

import pandas as pd

from screener.engine import StockScreener

# 短いフレームでも判定が走るよう ma_long を小さくした検証用設定
ENGINE_CFG = {
    "ma_short": 3, "ma_long": 5,
    "rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70,
    "volume_avg_period": 20, "volume_spike_ratio": 2.0,
    "breakout_lookback": 5,
    "min_price": 300, "min_avg_volume": 100_000,
}
TRADE_PLAN_CFG = {"atr_entry_pullback": 0.5, "atr_stop_mult": 2.0, "reward_risk_ratio": 2.0}
ORDER_CFG = {"entry_order_type": "IFDOCO", "exit_order_type": "OCO"}


def ohlcv(closes, volume=500_000):
    """終値リストから OHLCV DataFrame を作る（高値・安値は終値±1）"""
    return pd.DataFrame({
        "open":   closes,
        "high":   [c + 1 for c in closes],
        "low":    [c - 1 for c in closes],
        "close":  closes,
        "volume": [volume] * len(closes),
    })


# 末尾で上放れ → 高値ブレイク（買い候補）／下放れ → 安値ブレイク（手仕舞い）
BREAKOUT_UP = ohlcv([1000.0] * 29 + [1100.0])
BREAKOUT_DOWN = ohlcv([1000.0] * 29 + [900.0])
FLAT = ohlcv([1000.0] * 30)


class FakeDataClient:
    """code→DataFrame / code→info を返すだけのテスト用クライアント"""

    def __init__(self, histories, infos=None):
        self._histories = histories
        self._infos = infos or {}

    def get_history(self, code, market=None, period="6mo", interval="1d"):
        return self._histories.get(code)

    def get_info(self, code, market=None):
        return self._infos.get(code)


def make_screener(histories, infos=None):
    return StockScreener(FakeDataClient(histories, infos), ENGINE_CFG, TRADE_PLAN_CFG, ORDER_CFG)


class TestScanUniverse:
    def test_breakout_stock_is_returned_with_entry_order(self):
        screener = make_screener({"7203": BREAKOUT_UP}, {"7203": {"name": "トヨタ"}})
        results = screener.scan_universe(["7203"])
        assert len(results) == 1
        r = results[0]
        assert r["name"] == "トヨタ"
        assert "BREAKOUT_HIGH" in {s["type"] for s in r["signals"]}
        assert r["order"].order_type == "IFDOCO"   # 新規買い候補

    def test_market_is_autodetected_per_symbol(self):
        screener = make_screener({"7203": BREAKOUT_UP, "AAPL": BREAKOUT_UP})
        results = {r["code"]: r for r in screener.scan_universe(["7203", "AAPL"])}
        assert results["7203"]["market"].code == "JP"
        assert results["AAPL"]["market"].code == "US"

    def test_falls_back_to_code_when_info_missing(self):
        screener = make_screener({"7203": BREAKOUT_UP})  # infoなし
        assert screener.scan_universe(["7203"])[0]["name"] == "7203"

    def test_stock_without_signal_is_excluded(self):
        screener = make_screener({"7203": FLAT})
        assert screener.scan_universe(["7203"]) == []

    def test_stock_below_min_price_is_excluded(self):
        cheap = ohlcv([100.0] * 29 + [110.0])  # 上放れだが株価が下限未満
        screener = make_screener({"9999": cheap})
        assert screener.scan_universe(["9999"]) == []

    def test_us_stock_under_yen_floor_but_above_dollar_floor_is_included(self):
        # 米国株は min_price_us（ドル）で判定するため、¥300未満相当の$55でも対象になる。
        us = ohlcv([50.0] * 29 + [55.0])
        screener = make_screener({"AAPL": us}, {"AAPL": {"name": "Apple"}})
        results = screener.scan_universe(["AAPL"])
        assert len(results) == 1
        assert results[0]["market"].code == "US"

    def test_us_penny_stock_below_dollar_floor_is_excluded(self):
        # $5未満のペニー株は米国フロアで除外される。
        penny = ohlcv([3.0] * 29 + [4.5])
        screener = make_screener({"XYZ": penny})
        assert screener.scan_universe(["XYZ"]) == []

    def test_illiquid_stock_is_excluded(self):
        thin = ohlcv([1000.0] * 29 + [1100.0], volume=1_000)  # 出来高不足
        screener = make_screener({"7203": thin})
        assert screener.scan_universe(["7203"]) == []

    def test_too_short_history_is_skipped(self):
        screener = make_screener({"7203": ohlcv([1000.0] * 6)})
        assert screener.scan_universe(["7203"]) == []

    def test_missing_history_is_skipped(self):
        screener = make_screener({})  # get_history が None を返す
        assert screener.scan_universe(["7203"]) == []


class TestCheckHoldings:
    def test_sell_signal_produces_exit_oco_order(self):
        screener = make_screener({"7203": BREAKOUT_DOWN})
        holdings = [{"code": "7203", "name": "トヨタ", "avg_price": 800, "shares": 100}]
        r = screener.check_holdings(holdings)[0]
        assert "BREAKOUT_LOW" in {s["type"] for s in r["signals"]}
        assert r["order"].order_type == "OCO"   # 保有手仕舞い

    def test_buy_signal_on_holding_produces_entry_order(self):
        screener = make_screener({"7203": BREAKOUT_UP})
        holdings = [{"code": "7203", "name": "トヨタ", "avg_price": 1000, "shares": 50}]
        r = screener.check_holdings(holdings)[0]
        assert r["order"].order_type == "IFDOCO"   # 買い増し候補

    def test_unrealized_pl_is_computed_from_avg_price_and_shares(self):
        screener = make_screener({"7203": BREAKOUT_DOWN})  # 現値900
        holdings = [{"code": "7203", "name": "トヨタ", "avg_price": 800, "shares": 100}]
        r = screener.check_holdings(holdings)[0]
        assert r["unrealized_pct"] == (900 - 800) / 800 * 100   # +12.5%
        assert r["unrealized_amount"] == (900 - 800) * 100      # +10,000

    def test_amount_is_omitted_without_shares(self):
        screener = make_screener({"7203": BREAKOUT_DOWN})
        holdings = [{"code": "7203", "name": "トヨタ", "avg_price": 800}]
        r = screener.check_holdings(holdings)[0]
        assert r["unrealized_pct"] is not None
        assert r["unrealized_amount"] is None

    def test_holding_without_signal_is_kept_with_no_order(self):
        # スクリーニングと違い保有は無シグナルでも結果に残す（損益監視のため）
        screener = make_screener({"7203": FLAT})
        holdings = [{"code": "7203", "name": "トヨタ", "avg_price": 800, "shares": 100}]
        r = screener.check_holdings(holdings)[0]
        assert r["signals"] == []
        assert r["order"] is None

    def test_explicit_market_override_is_respected(self):
        screener = make_screener({"7203": FLAT})
        holdings = [{"code": "7203", "name": "X", "avg_price": 800, "market": "US"}]
        r = screener.check_holdings(holdings)[0]
        assert r["market"].code == "US"
