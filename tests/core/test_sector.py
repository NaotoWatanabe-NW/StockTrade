"""core.sector（合成業種インデックス）のテスト

価格取得には依存しない純粋関数なので、合成 OHLCV で決定的に検証する。
等ウェイト正規化・最低構成数・日付整合・ルックアヘッド無しを確認する。
"""

import pandas as pd
import pytest

from core.sector import build_sector_indices, sector_series_for


def _close_df(values, start="2024-01-01"):
    idx = pd.date_range(start, periods=len(values), freq="D")
    return pd.DataFrame({"close": values}, index=idx)


class TestBuildSectorIndices:
    def test_equal_weight_average_of_normalized_returns(self):
        # A は2倍、B/C は横ばい → 末尾は (2.0 + 1.0 + 1.0)/3
        dfs = {
            "A": _close_df([100.0, 200.0]),
            "B": _close_df([100.0, 100.0]),
            "C": _close_df([100.0, 100.0]),
        }
        groups = {"A": "G", "B": "G", "C": "G"}
        idx = build_sector_indices(dfs, groups, min_constituents=3)
        assert "G" in idx
        assert idx["G"]["close"].iloc[0] == pytest.approx(1.0)
        assert idx["G"]["close"].iloc[-1] == pytest.approx((2.0 + 1.0 + 1.0) / 3)

    def test_group_below_min_constituents_is_skipped(self):
        dfs = {"A": _close_df([100.0, 110.0]), "B": _close_df([100.0, 120.0])}
        idx = build_sector_indices(dfs, {"A": "G", "B": "G"}, min_constituents=3)
        assert idx == {}

    def test_codes_without_group_are_ignored(self):
        dfs = {
            "A": _close_df([100.0, 110.0]),
            "B": _close_df([100.0, 110.0]),
            "C": _close_df([100.0, 110.0]),
            "X": _close_df([100.0, 999.0]),   # グループ未指定 → 無視
        }
        idx = build_sector_indices(dfs, {"A": "G", "B": "G", "C": "G"}, min_constituents=3)
        assert set(idx) == {"G"}
        # X が無視されるので外れ値は混ざらない（全員 ×1.1）
        assert idx["G"]["close"].iloc[-1] == pytest.approx(1.1)

    def test_aligns_constituents_with_different_date_ranges(self):
        a = _close_df([100.0, 100.0, 100.0], start="2024-01-01")  # 1/1〜1/3
        b = _close_df([100.0, 100.0, 100.0], start="2024-01-03")  # 1/3〜1/5
        c = _close_df([100.0, 100.0, 100.0], start="2024-01-02")  # 1/2〜1/4
        idx = build_sector_indices({"A": a, "B": b, "C": c},
                                   {"A": "G", "B": "G", "C": "G"}, min_constituents=3)
        days = idx["G"].index
        assert str(days.min().date()) == "2024-01-01"
        assert str(days.max().date()) == "2024-01-05"

    def test_past_values_do_not_depend_on_future_bars(self):
        full = {
            "A": _close_df([100.0, 110.0, 120.0, 130.0]),
            "B": _close_df([100.0, 90.0, 80.0, 70.0]),
            "C": _close_df([100.0, 105.0, 110.0, 115.0]),
        }
        groups = {"A": "G", "B": "G", "C": "G"}
        idx_full = build_sector_indices(full, groups, min_constituents=3)["G"]
        # 後半を切り落とした入力でも、前半の値は一致する（未来参照なし）
        trunc = {k: v.iloc[:2] for k, v in full.items()}
        idx_trunc = build_sector_indices(trunc, groups, min_constituents=3)["G"]
        for d in idx_trunc.index:
            assert idx_full.loc[d, "close"] == pytest.approx(idx_trunc.loc[d, "close"])


class TestSectorSeriesFor:
    def test_returns_index_for_known_code(self):
        indices = {"G": _close_df([1.0, 1.1])}
        assert sector_series_for("A", {"A": "G"}, indices) is indices["G"]

    def test_returns_none_for_unknown_code(self):
        assert sector_series_for("Z", {"A": "G"}, {"G": _close_df([1.0])}) is None

    def test_returns_none_when_group_index_missing(self):
        # グループは判明するが合成インデックスが無い（薄くて未生成）場合
        assert sector_series_for("A", {"A": "G"}, {}) is None
