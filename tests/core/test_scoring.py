"""core.scoring（合議制スコアリング）のテスト

各コンポーネントの入力となる指標列を直接組んだフレームで、決定的に検証する。
"""

import math
import pandas as pd

from core.scoring import (
    compute_consensus, _label, _score_sector,
    DEFAULT_SCORING_CONFIG, DEFAULT_SECTOR_SCORING,
)

# 中立な指標行。末尾2足を上書きして特定の地合いを作る。
NEUTRAL = {
    "open": 100, "close": 100, "ma_short": 100, "ma_long": 100, "rsi": 50,
    "volume_ratio": 1.0, "highest_n": 120, "lowest_n": 80,
    "macd": 0.0, "macd_signal": 0.0, "macd_hist": 0.0,
}


def frame(prev=None, curr=None, n=15):
    rows = [dict(NEUTRAL) for _ in range(n)]
    if prev:
        rows[-2].update(prev)
    if curr:
        rows[-1].update(curr)
    return pd.DataFrame(rows)


# 全コンポーネントが +1 になる強気フレーム
BULLISH = frame(
    prev={"macd_hist": 0.5},
    curr={"ma_short": 114, "ma_long": 104, "close": 130, "open": 120, "rsi": 25,
          "volume_ratio": 2.0, "highest_n": 120, "lowest_n": 80,
          "macd": 2.0, "macd_signal": 1.0, "macd_hist": 1.0},
)
# 全コンポーネントが -1 になる弱気フレーム
BEARISH = frame(
    prev={"macd_hist": -0.5},
    curr={"ma_short": 86, "ma_long": 96, "close": 70, "open": 80, "rsi": 75,
          "volume_ratio": 2.0, "highest_n": 120, "lowest_n": 80,
          "macd": -2.0, "macd_signal": -1.0, "macd_hist": -1.0},
)


class TestLabel:
    def test_thresholds_map_to_labels(self):
        cfg = DEFAULT_SCORING_CONFIG
        assert _label(70, cfg) == ("STRONG_BUY", "強い買い", "BUY")
        assert _label(30, cfg) == ("BUY", "買い", "BUY")
        assert _label(0, cfg) == ("NEUTRAL", "中立", "NEUTRAL")
        assert _label(-30, cfg) == ("SELL", "売り", "SELL")
        assert _label(-70, cfg) == ("STRONG_SELL", "強い売り", "SELL")


class TestConsensusDirection:
    def test_all_bullish_components_give_strong_buy(self):
        c = compute_consensus(BULLISH, DEFAULT_SCORING_CONFIG)
        assert c.score == 100.0
        assert c.side == "BUY" and c.label == "STRONG_BUY"

    def test_all_bearish_components_give_strong_sell(self):
        c = compute_consensus(BEARISH, DEFAULT_SCORING_CONFIG)
        assert c.score == -100.0
        assert c.side == "SELL" and c.label == "STRONG_SELL"

    def test_components_cover_all_five_indicators(self):
        c = compute_consensus(BULLISH, DEFAULT_SCORING_CONFIG)
        assert {comp.name for comp in c.components} == {
            "trend", "macd", "rsi", "volume", "breakout"
        }


class TestNaNHandling:
    def test_component_with_nan_inputs_is_skipped(self):
        df = BULLISH.copy()
        df.loc[df.index[-1], ["macd", "macd_signal", "macd_hist"]] = math.nan
        c = compute_consensus(df, DEFAULT_SCORING_CONFIG)
        names = {comp.name for comp in c.components}
        assert "macd" not in names           # 欠損のmacdは除外
        assert c.side == "BUY"               # 残りで強気は維持

    def test_returns_none_when_all_components_unavailable(self):
        df = frame(curr={k: math.nan for k in NEUTRAL})
        assert compute_consensus(df, DEFAULT_SCORING_CONFIG) is None


class TestWeightsAreConfigurable:
    # trend=+1 / breakout=-1 が混在するフレーム（重みで符号が変わることを確認）
    MIXED = frame(
        prev={"macd_hist": 0.0},
        curr={"ma_short": 114, "ma_long": 104, "close": 110,
              "highest_n": 130, "lowest_n": 120},  # close<lowest_n → breakout -1
    )

    def _cfg(self, **weights):
        base = {"trend": 0, "macd": 0, "rsi": 0, "volume": 0, "breakout": 0}
        base.update(weights)
        return {**DEFAULT_SCORING_CONFIG, "weights": base}

    def test_trend_weight_dominates_to_buy(self):
        c = compute_consensus(self.MIXED, self._cfg(trend=1))
        assert c.score == 100.0 and c.side == "BUY"

    def test_breakout_weight_dominates_to_sell(self):
        c = compute_consensus(self.MIXED, self._cfg(breakout=1))
        assert c.score == -100.0 and c.side == "SELL"


# 短い期間でも算出できる業種スコア設定（テスト用）
SECTOR_CFG = {"index_ma": 5, "ma_slope_lookback": 3, "rs_lookback": 5, "rs_scale": 0.10,
              "trend_weight": 0.5, "rs_weight": 0.5}


def _dated_close_df(values, start="2024-01-01"):
    idx = pd.date_range(start, periods=len(values), freq="D")
    return pd.DataFrame({"close": values}, index=idx)


class TestSectorComponent:
    # 株価・業種ともに同じ日付軸を持つ8本。業種は緩やかな上昇/下降を作る。
    STOCK_UP = _dated_close_df([100, 101, 102, 103, 104, 105, 106, 110])
    SECTOR_UP = _dated_close_df([100, 100.5, 101, 101.5, 102, 102.5, 103, 103.5])
    SECTOR_DOWN = _dated_close_df([100, 99.5, 99, 98.5, 98, 97.5, 97, 96.5])

    def test_uptrend_sector_gives_positive_component(self):
        comp = _score_sector(self.STOCK_UP, self.SECTOR_UP, DEFAULT_SCORING_CONFIG, SECTOR_CFG)
        assert comp is not None and comp.name == "sector"
        assert comp.score > 0          # 業種が上昇トレンド＋銘柄がアウトパフォーム

    def test_downtrend_sector_gives_negative_component(self):
        # 銘柄も業種より弱くする（横ばい）と相対強度も負に寄る
        stock_flat = _dated_close_df([100] * 8)
        comp = _score_sector(stock_flat, self.SECTOR_DOWN, DEFAULT_SCORING_CONFIG, SECTOR_CFG)
        assert comp is not None
        assert comp.score < 0          # 業種が下降トレンド

    def test_insufficient_sector_history_returns_none(self):
        short_sector = _dated_close_df([100, 101, 102])   # index_ma/rs_lookback に満たない
        comp = _score_sector(self.STOCK_UP, short_sector, DEFAULT_SCORING_CONFIG, SECTOR_CFG)
        assert comp is None

    def test_uses_default_sector_scoring_when_cfg_omitted(self):
        # sector_cfg 省略時は DEFAULT_SECTOR_SCORING（index_ma=50 等）で要求期間が長い
        comp = _score_sector(self.STOCK_UP, self.SECTOR_UP, DEFAULT_SCORING_CONFIG, None)
        assert comp is None            # 8本では既定の50期間に満たず None
        assert DEFAULT_SECTOR_SCORING["index_ma"] == 50


class TestConsensusWithSector:
    STOCK_UP = _dated_close_df([100, 101, 102, 103, 104, 105, 106, 110])
    SECTOR_UP = _dated_close_df([100, 100.5, 101, 101.5, 102, 102.5, 103, 103.5])

    def _stock_frame(self):
        # 合議スコアの5成分が計算できる指標列を、業種スコア用の日付軸に載せる
        rows = [dict(NEUTRAL) for _ in range(len(self.STOCK_UP))]
        df = pd.DataFrame(rows, index=self.STOCK_UP.index)
        df["close"] = self.STOCK_UP["close"].values
        return df

    def test_sector_component_absent_without_df_sector(self):
        c = compute_consensus(self._stock_frame(), DEFAULT_SCORING_CONFIG)
        assert "sector" not in {comp.name for comp in c.components}

    def test_sector_component_present_with_df_sector(self):
        c = compute_consensus(self._stock_frame(), DEFAULT_SCORING_CONFIG,
                              df_sector=self.SECTOR_UP, sector_cfg=SECTOR_CFG)
        assert "sector" in {comp.name for comp in c.components}
