"""core.exit_rules のテスト"""

import pytest
from core.exit_rules import ExitState, ExitSignal, update_exit

CFG = {
    "time_stop_days":    10,
    "partial_tp_r":      1.0,
    "partial_tp_pct":    0.5,
    "move_to_breakeven": True,
    "trail_atr_mult":    2.0,
}


def make_state(entry=1000.0, stop=980.0, target=1040.0, atr=10.0) -> ExitState:
    return ExitState(
        entry_price=entry,
        initial_stop=stop,
        current_stop=stop,
        target=target,
        atr=atr,
    )


def tick(state, lo, hi, close=None, atr=10.0) -> ExitSignal:
    """1バー分を更新する補助関数。close は lo+hi の中値がデフォルト。"""
    if close is None:
        close = (lo + hi) / 2
    return update_exit(state, lo, hi, close, atr, CFG)


class TestTimeStop:
    def test_triggers_at_time_stop_days(self):
        state = make_state()
        # time_stop_days=10 なので 9 バーは手仕舞いなし
        for _ in range(9):
            sig = tick(state, lo=985, hi=1005)
            assert sig.reason is None
        # 10 バー目でタイムストップ
        sig = tick(state, lo=985, hi=1005, close=1002)
        assert sig.reason == "TIME_STOP"
        assert sig.exit_price == 1002

    def test_time_stop_takes_priority_over_stop_loss(self):
        """タイムストップ発動バーで同時に損切りラインを下抜いても TIME_STOP"""
        state = make_state()
        for _ in range(9):
            tick(state, lo=985, hi=1005)
        sig = tick(state, lo=975, hi=990, close=988)  # lo < stop(980)
        assert sig.reason == "TIME_STOP"


class TestStopLoss:
    def test_triggers_when_low_hits_stop(self):
        state = make_state()
        sig = tick(state, lo=979, hi=1005)  # lo(979) <= stop(980)
        assert sig.reason == "STOP_LOSS"
        assert sig.exit_price == 980.0

    def test_no_stop_when_low_above_stop(self):
        state = make_state()
        sig = tick(state, lo=981, hi=1005)
        assert sig.reason is None


class TestPartialTakeProfit:
    def test_partial_tp_triggers_at_1r(self):
        """高値が entry + 1R(=20) 以上になれば第1利確"""
        state = make_state(entry=1000, stop=980, target=1040)
        # partial_target = 1000 + 1.0 * 20 = 1020
        sig = tick(state, lo=990, hi=1025)
        assert sig.partial_tp_price == pytest.approx(1020.0)
        assert sig.partial_tp_pct == 0.5

    def test_partial_tp_moves_stop_to_at_least_breakeven(self):
        """部分利確後に current_stop が entry_price 以上に引き上げられる。
        同バーでトレーリングが走り建値を超えることがあるため >= で確認する。"""
        state = make_state(entry=1000, stop=980, target=1040)
        tick(state, lo=990, hi=1025)  # 部分利確発生
        assert state.current_stop >= 1000.0  # 少なくとも建値以上
        assert state.partial_taken is True

    def test_partial_tp_not_taken_twice(self):
        """部分利確は1回だけ"""
        state = make_state(entry=1000, stop=980, target=1040)
        tick(state, lo=990, hi=1025)
        sig2 = tick(state, lo=1005, hi=1030)
        assert sig2.partial_tp_price is None


class TestTrailingStop:
    def test_trailing_stop_updates_after_partial_tp(self):
        """部分利確後にトレーリングが動く"""
        state = make_state(entry=1000, stop=980, target=1060, atr=10.0)
        # 第1利確発生（1020タッチ）→ 同バーでトレーリングも走り stop が上昇する
        tick(state, lo=990, hi=1025)
        old_stop = state.current_stop  # この時点では 1005.0 (trail = 1025-20)
        # lo を old_stop より上にして損切りを回避しつつ、高値を上げてトレーリング更新
        # trail_stop = 1050 - 2*10 = 1030 > 1005 → stop が上がるはず
        sig = tick(state, lo=old_stop + 1, hi=1050, atr=10.0)
        assert sig.reason is None or sig.reason == "TAKE_PROFIT"  # 損切りではない
        assert state.current_stop > old_stop

    def test_trailing_stop_does_not_move_before_partial_tp(self):
        """部分利確前はトレーリングしない"""
        state = make_state()
        tick(state, lo=990, hi=1010)  # partial_target(1020)未達
        assert state.current_stop == 980.0  # 変化なし


class TestFinalTakeProfit:
    def test_take_profit_triggers_at_target(self):
        state = make_state(entry=1000, stop=980, target=1040)
        sig = tick(state, lo=1005, hi=1045)
        assert sig.reason == "TAKE_PROFIT"
        assert sig.exit_price == pytest.approx(1040.0)

    def test_stop_loss_takes_priority_over_take_profit_on_same_bar(self):
        """同一バーで stop と target 両方タッチ → stop_loss が優先"""
        state = make_state(entry=1000, stop=980, target=1040)
        sig = tick(state, lo=975, hi=1045)  # gap で両方タッチ
        assert sig.reason == "STOP_LOSS"
