"""core.orders（SBI注文タイプへの組み立て）のテスト"""

from core.market import JP
from core.orders import (
    Leg,
    build_entry_order,
    build_exit_order,
    build_order,
)

# 代表的な価格プラン（trade_plan の出力形式）
BUY_LIMIT_PLAN = {
    "side": "BUY", "entry_kind": "LIMIT",
    "entry": 990, "stop": 950, "target": 1070,
    "risk_pct": 4.0, "reward_pct": 8.0,
}
BUY_STOP_PLAN = {**BUY_LIMIT_PLAN, "entry_kind": "STOP", "entry": 1010}
SELL_PLAN = {
    "side": "SELL", "entry_kind": "LIMIT",
    "entry": 1010, "stop": 960, "target": None,
    "risk_pct": 4.0, "reward_pct": None,
}

ORDER_CFG = {"entry_order_type": "IFDOCO", "exit_order_type": "OCO"}


def roles(order):
    return [leg.role for leg in order.legs]


class TestBuildEntryOrder:
    def test_ifdoco_has_entry_take_profit_and_stop_legs(self):
        order = build_entry_order(BUY_LIMIT_PLAN, "IFDOCO")
        assert order.order_type == "IFDOCO"
        assert roles(order) == ["新規買い", "利確", "損切り"]

    def test_ifdoco_entry_leg_reflects_limit_kind(self):
        order = build_entry_order(BUY_LIMIT_PLAN, "IFDOCO")
        entry = order.legs[0]
        assert entry.side == "BUY" and entry.kind == "LIMIT" and entry.price == 990

    def test_ifdoco_entry_leg_reflects_stop_kind_for_breakout(self):
        order = build_entry_order(BUY_STOP_PLAN, "IFDOCO")
        assert order.legs[0].kind == "STOP" and order.legs[0].price == 1010

    def test_ifdoco_exit_legs_are_sell_side(self):
        order = build_entry_order(BUY_LIMIT_PLAN, "IFDOCO")
        assert order.legs[1].side == "SELL" and order.legs[1].kind == "LIMIT"  # 利確
        assert order.legs[2].side == "SELL" and order.legs[2].kind == "STOP"   # 損切り

    def test_ifdoco_without_target_omits_take_profit_leg(self):
        plan = {**BUY_LIMIT_PLAN, "target": None}
        order = build_entry_order(plan, "IFDOCO")
        assert roles(order) == ["新規買い", "損切り"]

    def test_ifd_has_entry_and_stop_only(self):
        order = build_entry_order(BUY_LIMIT_PLAN, "IFD")
        assert order.order_type == "IFD"
        assert roles(order) == ["新規買い", "損切り"]

    def test_simple_has_entry_leg_only_named_by_kind(self):
        order = build_entry_order(BUY_LIMIT_PLAN, "SIMPLE")
        assert order.order_type == "指値"
        assert roles(order) == ["新規買い"]

    def test_simple_uses_stop_label_for_breakout_entry(self):
        order = build_entry_order(BUY_STOP_PLAN, "SIMPLE")
        assert order.order_type == "逆指値"


class TestBuildExitOrder:
    def test_oco_has_take_profit_and_stop_legs(self):
        order = build_exit_order(SELL_PLAN, "OCO")
        assert order.order_type == "OCO"
        assert roles(order) == ["利確/戻り売り", "損切り/撤退"]
        assert all(leg.side == "SELL" for leg in order.legs)

    def test_oco_take_profit_is_limit_and_stop_is_stop(self):
        order = build_exit_order(SELL_PLAN, "OCO")
        assert order.legs[0].kind == "LIMIT" and order.legs[0].price == 1010
        assert order.legs[1].kind == "STOP" and order.legs[1].price == 960

    def test_stop_only_keeps_single_stop_leg(self):
        order = build_exit_order(SELL_PLAN, "STOP")
        assert order.order_type == "逆指値"
        assert roles(order) == ["損切り/撤退"]
        assert order.legs[0].kind == "STOP"


class TestBuildOrderRouting:
    def test_entry_context_with_buy_builds_entry_order(self):
        order = build_order("ENTRY", BUY_LIMIT_PLAN, ORDER_CFG)
        assert order.order_type == "IFDOCO"

    def test_exit_context_with_sell_builds_exit_order(self):
        order = build_order("EXIT", SELL_PLAN, ORDER_CFG)
        assert order.order_type == "OCO"

    def test_entry_context_with_sell_returns_none(self):
        # long-only 前提: 空売り新規は提案しない
        assert build_order("ENTRY", SELL_PLAN, ORDER_CFG) is None

    def test_exit_context_with_buy_returns_none(self):
        assert build_order("EXIT", BUY_LIMIT_PLAN, ORDER_CFG) is None

    def test_none_plan_returns_none(self):
        assert build_order("ENTRY", None, ORDER_CFG) is None


class TestLegText:
    def test_buy_limit_leg_renders_in_japanese_with_currency(self):
        leg = Leg("BUY", "LIMIT", 990, "新規買い")
        assert leg.text(JP) == "新規買い: 買い指値 ¥990"

    def test_sell_stop_leg_renders_with_reverse_limit_label(self):
        leg = Leg("SELL", "STOP", 950, "損切り")
        assert leg.text(JP) == "損切り: 売り逆指値 ¥950"
