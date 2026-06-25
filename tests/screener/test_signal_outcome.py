"""screener.signal_outcome（シグナルの予測 vs 実勢価格の評価）のテスト

評価ロジックの境界（約定判定・利確/損切の先着・期間満了・未約定・暫定）を
小さな OHLC データで検証する。価格取得はせず DataFrame を直接渡す。
"""

import pandas as pd
import pytest

from screener.signal_outcome import (
    evaluate_signal_outcome, NO_ENTRY, TARGET, STOP, TIMEOUT, PENDING,
)

# 共通のシグナル: BUY, entry=1000, stop=950(risk=50), target=1100(RR=2), LIMIT
SIG = {
    "side": "BUY", "entry_price": 1000.0, "stop_price": 950.0,
    "target_price": 1100.0, "risk": 50.0, "entry_kind": "LIMIT",
}


def _df(bars: list[dict], start: str = "2026-01-05") -> pd.DataFrame:
    """OHLC 行のリストから営業日インデックスの DataFrame を作る。"""
    idx = pd.bdate_range(start=start, periods=len(bars))
    return pd.DataFrame(bars, index=idx)


def test_returns_none_for_sell_signal():
    sell = dict(SIG, side="SELL")
    assert evaluate_signal_outcome(sell, _df([{"high": 1, "low": 1, "close": 1}]), 5, 5) is None


def test_returns_none_when_risk_undefined():
    bad = dict(SIG, entry_price=None, stop_price=None, risk=None)
    assert evaluate_signal_outcome(bad, _df([{"high": 1, "low": 1, "close": 1}]), 5, 5) is None


def test_target_hit_resolves_as_target_with_rr():
    df = _df([
        {"high": 1010, "low": 1000, "close": 1005},  # d0: LIMIT 約定（安値<=1000）
        {"high": 1050, "low": 1005, "close": 1040},  # d1: 未決着
        {"high": 1100, "low": 1050, "close": 1090},  # d2: 利確到達（高値>=1100）
    ])
    o = evaluate_signal_outcome(SIG, df, horizon_days=5, entry_valid_days=5)
    assert o["entry_filled"] is True
    assert o["outcome"] == TARGET
    assert o["hit_target"] is True and o["hit_stop"] is False
    assert o["days_to_resolve"] == 2          # 約定翌足から2本目
    assert o["realized_r"] == pytest.approx(2.0)


def test_stop_hit_resolves_as_stop_with_minus_one_r():
    df = _df([
        {"high": 1010, "low": 1000, "close": 1005},  # 約定
        {"high": 1010, "low": 950, "close": 960},    # 損切到達
    ])
    o = evaluate_signal_outcome(SIG, df, horizon_days=5, entry_valid_days=5)
    assert o["outcome"] == STOP
    assert o["hit_stop"] is True
    assert o["days_to_resolve"] == 1
    assert o["realized_r"] == pytest.approx(-1.0)


def test_same_bar_touching_both_is_conservatively_stop():
    df = _df([
        {"high": 1010, "low": 1000, "close": 1005},  # 約定
        {"high": 1100, "low": 950, "close": 1000},   # 同一足で利確・損切の両方に触れる
    ])
    o = evaluate_signal_outcome(SIG, df, horizon_days=5, entry_valid_days=5)
    assert o["outcome"] == STOP          # 悲観評価で STOP 優先
    assert o["hit_stop"] is True


def test_timeout_uses_close_at_horizon_for_r():
    df = _df([
        {"high": 1010, "low": 1000, "close": 1005},  # 約定
        {"high": 1040, "low": 990, "close": 1020},   # post j=0
        {"high": 1050, "low": 1000, "close": 1030},  # post j=1
        {"high": 1060, "low": 1010, "close": 1025},  # post j=2（horizon=3 の末足）
    ])
    o = evaluate_signal_outcome(SIG, df, horizon_days=3, entry_valid_days=5)
    assert o["outcome"] == TIMEOUT
    assert o["close_at_horizon"] == pytest.approx(1025)
    assert o["realized_r"] == pytest.approx((1025 - 1000) / 50)
    assert o["days_to_resolve"] == 3


def test_no_entry_when_limit_never_touched():
    df = _df([
        {"high": 1100, "low": 1010, "close": 1050},
        {"high": 1120, "low": 1030, "close": 1080},
        {"high": 1130, "low": 1040, "close": 1090},
    ])
    o = evaluate_signal_outcome(SIG, df, horizon_days=5, entry_valid_days=3)
    assert o["entry_filled"] is False
    assert o["outcome"] == NO_ENTRY


def test_pending_when_entry_window_not_yet_elapsed():
    # まだ entry_valid_days 分のバーが揃っておらず、未約定 → 確定保留
    df = _df([{"high": 1100, "low": 1010, "close": 1050}])
    o = evaluate_signal_outcome(SIG, df, horizon_days=5, entry_valid_days=5)
    assert o["entry_filled"] is False
    assert o["outcome"] == PENDING


def test_pending_when_filled_but_horizon_incomplete():
    df = _df([
        {"high": 1010, "low": 1000, "close": 1005},  # 約定
        {"high": 1040, "low": 990, "close": 1020},   # 未決着、horizon 未満
    ])
    o = evaluate_signal_outcome(SIG, df, horizon_days=5, entry_valid_days=5)
    assert o["entry_filled"] is True
    assert o["outcome"] == PENDING
    assert o["mfe_r"] == pytest.approx((1040 - 1000) / 50)   # 最大含み益
    assert o["mae_r"] == pytest.approx((990 - 1000) / 50)    # 最大含み損


def test_stop_entry_kind_fills_on_breakout_high():
    sig = dict(SIG, entry_kind="STOP")  # 逆指値: 高値>=entry で約定
    df = _df([
        {"high": 1000, "low": 980, "close": 995},    # d0: 高値=1000 で約定
        {"high": 1100, "low": 1010, "close": 1090},  # d1: 利確
    ])
    o = evaluate_signal_outcome(sig, df, horizon_days=5, entry_valid_days=5)
    assert o["entry_filled"] is True
    assert o["outcome"] == TARGET
