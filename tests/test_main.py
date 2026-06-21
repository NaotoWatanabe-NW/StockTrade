"""main.should_notify_holding（通知の絞り込み判定）のテスト"""

from main import should_notify_holding

CFG = {"suppress_neutral_holdings": True}


def result(signals, long_term=False):
    return {"signals": signals, "long_term": long_term}


def buy():
    return {"side": "BUY", "type": "GOLDEN_CROSS"}


def sell():
    return {"side": "SELL", "type": "DEAD_CROSS"}


def neutral():
    return {"side": "NEUTRAL", "type": "VOLUME_SPIKE"}


class TestShouldNotifyHolding:
    def test_no_signal_is_not_notified(self):
        assert should_notify_holding(result([]), CFG) is False

    def test_buy_signal_is_notified(self):
        assert should_notify_holding(result([buy()]), CFG) is True

    def test_sell_signal_is_notified_for_swing_holding(self):
        assert should_notify_holding(result([sell()]), CFG) is True

    def test_neutral_only_signal_is_suppressed(self):
        assert should_notify_holding(result([neutral()]), CFG) is False

    def test_neutral_can_be_allowed_by_config(self):
        assert should_notify_holding(result([neutral()]), {"suppress_neutral_holdings": False}) is True

    def test_long_term_holding_suppresses_sell(self):
        # 長期保有は売り/手仕舞いを通知しない
        assert should_notify_holding(result([sell()], long_term=True), CFG) is False

    def test_long_term_holding_still_notifies_buy(self):
        # 長期保有でも買い増しタイミングは通知する
        assert should_notify_holding(result([buy()], long_term=True), CFG) is True

    def test_mixed_signals_use_net_direction(self):
        # 売り2・買い1 → 総合SELL。長期保有なら抑制
        sigs = [sell(), sell(), buy()]
        assert should_notify_holding(result(sigs, long_term=True), CFG) is False
        assert should_notify_holding(result(sigs, long_term=False), CFG) is True
