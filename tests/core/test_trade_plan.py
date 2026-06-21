"""core.trade_plan（売買方向の集約・エントリー様式・価格算出）のテスト"""

import pytest

from core.trade_plan import net_side, entry_style, build_trade_plan

# テスト用パラメータ: 押し目0.5ATR、損切り2ATR、リスクリワード2:1
CFG = {"atr_entry_pullback": 0.5, "atr_stop_mult": 2.0, "reward_risk_ratio": 2.0}


def sig(side=None, type=None):
    return {"side": side, "type": type}


class TestNetSide:
    def test_buy_majority_returns_buy(self):
        assert net_side([sig("BUY"), sig("BUY"), sig("SELL")]) == "BUY"

    def test_sell_majority_returns_sell(self):
        assert net_side([sig("SELL"), sig("SELL"), sig("BUY")]) == "SELL"

    def test_tie_returns_neutral(self):
        assert net_side([sig("BUY"), sig("SELL")]) == "NEUTRAL"

    def test_empty_returns_neutral(self):
        assert net_side([]) == "NEUTRAL"

    def test_neutral_signals_are_ignored(self):
        assert net_side([sig("NEUTRAL"), sig("BUY")]) == "BUY"


class TestEntryStyle:
    def test_breakout_high_returns_breakout(self):
        assert entry_style([sig(type="BREAKOUT_HIGH")]) == "BREAKOUT"

    def test_breakout_low_returns_breakout(self):
        assert entry_style([sig(type="BREAKOUT_LOW")]) == "BREAKOUT"

    def test_non_breakout_returns_pullback(self):
        assert entry_style([sig(type="GOLDEN_CROSS")]) == "PULLBACK"

    def test_breakout_takes_priority_when_mixed(self):
        assert entry_style([sig(type="RSI_REBOUND"), sig(type="BREAKOUT_HIGH")]) == "BREAKOUT"

    def test_empty_returns_pullback(self):
        assert entry_style([]) == "PULLBACK"


class TestBuildTradePlanBuy:
    def test_pullback_buy_places_entry_below_price_as_limit(self):
        p = build_trade_plan("BUY", price=1000, atr=20, cfg=CFG, style="PULLBACK")
        assert p["entry_kind"] == "LIMIT"
        assert p["entry"] == 990          # 1000 - 0.5*20
        assert p["stop"] == 950           # 990 - 2*20
        assert p["target"] == 1070        # 990 + 2*(990-950)

    def test_breakout_buy_places_entry_above_price_as_stop(self):
        p = build_trade_plan("BUY", price=1000, atr=20, cfg=CFG, style="BREAKOUT")
        assert p["entry_kind"] == "STOP"
        assert p["entry"] == 1010         # 1000 + 0.5*20
        assert p["stop"] == 970           # 1010 - 2*20
        assert p["target"] == 1090        # 1010 + 2*(1010-970)

    def test_reward_is_ratio_times_risk(self):
        p = build_trade_plan("BUY", price=1000, atr=20, cfg=CFG)
        # 利確幅 = リスクリワード比 × リスク幅（％でも同じ関係が成り立つ）
        assert p["reward_pct"] == pytest.approx(CFG["reward_risk_ratio"] * p["risk_pct"])

    def test_risk_pct_matches_entry_and_stop(self):
        p = build_trade_plan("BUY", price=1000, atr=20, cfg=CFG)
        assert p["risk_pct"] == pytest.approx((p["entry"] - p["stop"]) / p["entry"] * 100)


class TestBuildTradePlanSell:
    def test_sell_places_take_above_and_stop_below_price(self):
        p = build_trade_plan("SELL", price=1000, atr=20, cfg=CFG)
        assert p["side"] == "SELL"
        assert p["entry"] == 1010         # 戻り売り（利確）= 1000 + 0.5*20
        assert p["stop"] == 960           # 撤退（損切り）= 1000 - 2*20
        assert p["target"] is None        # 売り手仕舞いに上値目標は持たせない

    def test_sell_risk_pct_is_relative_to_current_price(self):
        p = build_trade_plan("SELL", price=1000, atr=20, cfg=CFG)
        assert p["risk_pct"] == pytest.approx((1000 - 960) / 1000 * 100)


class TestBuildTradePlanInvalid:
    def test_neutral_side_returns_none(self):
        assert build_trade_plan("NEUTRAL", 1000, 20, CFG) is None

    def test_missing_atr_returns_none(self):
        assert build_trade_plan("BUY", 1000, None, CFG) is None

    def test_non_positive_atr_returns_none(self):
        assert build_trade_plan("BUY", 1000, 0, CFG) is None
        assert build_trade_plan("BUY", 1000, -5, CFG) is None

    def test_non_positive_price_returns_none(self):
        assert build_trade_plan("BUY", 0, 20, CFG) is None
