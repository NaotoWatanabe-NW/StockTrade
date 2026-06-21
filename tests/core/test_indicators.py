"""core.indicators（指標計算・シグナル判定）のテスト"""

import pandas as pd

from core.indicators import add_technical_indicators, detect_signals

# detect_signals は最新2足の指標列だけを見るため、ma_long を小さくして
# 短いフレームでも判定が走るようにする
CFG = {
    "ma_short": 2, "ma_long": 3,
    "rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70,
    "volume_avg_period": 20, "volume_spike_ratio": 2.0,
    "breakout_lookback": 5,
}

# シグナルが出ない中立な1足。各テストで末尾2足だけ上書きして特定条件を作る
NEUTRAL_ROW = {
    "open": 100, "close": 100,
    "ma_short": 100, "ma_long": 100, "rsi": 50,
    "volume_ratio": 1.0, "highest_n": 200, "lowest_n": 50,
}


def frame(prev=None, curr=None, n=6):
    """中立な n 足を作り、末尾2足を上書きして判定対象フレームを作る"""
    rows = [dict(NEUTRAL_ROW) for _ in range(n)]
    if prev:
        rows[-2].update(prev)
    if curr:
        rows[-1].update(curr)
    return pd.DataFrame(rows)


def types(signals):
    return {s["type"] for s in signals}


class TestAddTechnicalIndicators:
    def test_adds_all_indicator_columns(self):
        df = pd.DataFrame({
            "open":  [100, 101, 102, 103, 104],
            "high":  [101, 102, 103, 104, 105],
            "low":   [99, 100, 101, 102, 103],
            "close": [100, 101, 102, 103, 104],
            "volume": [1000, 1100, 1200, 1300, 1400],
        })
        out = add_technical_indicators(df, CFG)
        for col in ["ma_short", "ma_long", "rsi", "volume_avg",
                    "volume_ratio", "highest_n", "lowest_n", "atr",
                    "macd", "macd_signal", "macd_hist"]:
            assert col in out.columns

    def test_short_ma_equals_rolling_mean(self):
        df = pd.DataFrame({
            "open": [10, 20, 30, 40],
            "high": [10, 20, 30, 40],
            "low": [10, 20, 30, 40],
            "close": [10, 20, 30, 40],
            "volume": [1, 1, 1, 1],
        })
        out = add_technical_indicators(df, CFG)
        # ma_short(=2) の最終値は直近2終値の平均
        assert out["ma_short"].iloc[-1] == 35  # (30 + 40) / 2

    def test_highest_n_excludes_current_bar(self):
        df = pd.DataFrame({
            "open": [1, 2, 3, 4, 5, 6, 7],
            "high": [1, 2, 3, 4, 5, 6, 7],
            "low": [1, 2, 3, 4, 5, 6, 7],
            "close": [1, 2, 3, 4, 5, 6, 7],
            "volume": [1] * 7,
        })
        out = add_technical_indicators(df, CFG)
        # 当日(=7)を除いた直近5本の最高値は6
        assert out["highest_n"].iloc[-1] == 6


class TestDetectSignalsCrosses:
    def test_golden_cross_when_short_ma_crosses_above_long(self):
        df = frame(prev={"ma_short": 99, "ma_long": 100},
                   curr={"ma_short": 101, "ma_long": 100})
        signals = detect_signals(df, CFG)
        assert types(signals) == {"GOLDEN_CROSS"}
        assert signals[0]["side"] == "BUY"

    def test_dead_cross_when_short_ma_crosses_below_long(self):
        df = frame(prev={"ma_short": 101, "ma_long": 100},
                   curr={"ma_short": 99, "ma_long": 100})
        signals = detect_signals(df, CFG)
        assert types(signals) == {"DEAD_CROSS"}
        assert signals[0]["side"] == "SELL"


class TestDetectSignalsRsi:
    def test_rsi_rebound_when_crossing_up_through_oversold(self):
        df = frame(prev={"rsi": 25}, curr={"rsi": 35})
        signals = detect_signals(df, CFG)
        assert types(signals) == {"RSI_REBOUND"}
        assert signals[0]["side"] == "BUY"

    def test_rsi_pullback_when_crossing_down_through_overbought(self):
        df = frame(prev={"rsi": 75}, curr={"rsi": 65})
        signals = detect_signals(df, CFG)
        assert types(signals) == {"RSI_PULLBACK"}
        assert signals[0]["side"] == "SELL"

    def test_no_rsi_signal_while_staying_in_neutral_band(self):
        df = frame(prev={"rsi": 45}, curr={"rsi": 55})
        assert detect_signals(df, CFG) == []


class TestDetectSignalsVolumeAndBreakout:
    def test_volume_spike_is_neutral_signal(self):
        df = frame(curr={"volume_ratio": 3.0, "close": 105, "open": 100})
        signals = detect_signals(df, CFG)
        assert types(signals) == {"VOLUME_SPIKE"}
        assert signals[0]["side"] == "NEUTRAL"
        assert "上昇" in signals[0]["label"]

    def test_volume_spike_labels_down_bar(self):
        df = frame(curr={"volume_ratio": 3.0, "close": 95, "open": 100})
        signals = detect_signals(df, CFG)
        assert "下落" in signals[0]["label"]

    def test_breakout_high_when_close_exceeds_prior_high(self):
        df = frame(curr={"close": 250, "highest_n": 200})
        signals = detect_signals(df, CFG)
        assert "BREAKOUT_HIGH" in types(signals)

    def test_breakout_low_when_close_below_prior_low(self):
        df = frame(curr={"close": 40, "lowest_n": 50})
        signals = detect_signals(df, CFG)
        assert "BREAKOUT_LOW" in types(signals)


class TestDetectSignalsGuards:
    def test_returns_empty_when_frame_too_short(self):
        # ma_long(3) + 2 = 5 足未満なら判定しない
        df = frame(prev={"ma_short": 99, "ma_long": 100},
                   curr={"ma_short": 101, "ma_long": 100}, n=4)
        assert detect_signals(df, CFG) == []

    def test_multiple_signals_can_co_occur(self):
        df = frame(
            prev={"ma_short": 99, "ma_long": 100},
            curr={"ma_short": 101, "ma_long": 100, "close": 250,
                  "highest_n": 200, "volume_ratio": 3.0, "open": 100},
        )
        signals = detect_signals(df, CFG)
        assert {"GOLDEN_CROSS", "BREAKOUT_HIGH", "VOLUME_SPIKE"} <= types(signals)
