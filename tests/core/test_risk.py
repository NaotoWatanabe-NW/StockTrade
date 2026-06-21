"""core.risk のテスト"""

import pytest
from core.risk import calc_shares, lot_size_for_market, calc_position_value, heat


class TestCalcShares:
    def test_basic_calculation(self):
        # 口座100万円, リスク1%, entry=1000, stop=980 → risk_per_share=20
        # max_risk = 10,000円 → shares = 10,000 / 20 = 500株
        shares = calc_shares(1_000_000, 1.0, 1000, 980, lot_size=1)
        assert shares == 500

    def test_rounds_down_to_lot_size(self):
        # shares_raw = 10,000 / 20 = 500 → lot_size=100 → 500株（端数なし）
        shares = calc_shares(1_000_000, 1.0, 1000, 980, lot_size=100)
        assert shares == 500
        assert shares % 100 == 0

    def test_rounds_down_fractional_lots(self):
        # risk_per_share=22 → shares_raw ≈ 454.5 → lot_size=100 → 400株
        shares = calc_shares(1_000_000, 1.0, 1000, 978, lot_size=100)
        assert shares == 400

    def test_returns_zero_when_stop_equals_entry(self):
        shares = calc_shares(1_000_000, 1.0, 1000, 1000, lot_size=1)
        assert shares == 0

    def test_returns_zero_when_account_is_zero(self):
        shares = calc_shares(0, 1.0, 1000, 980, lot_size=1)
        assert shares == 0

    def test_higher_risk_pct_gives_more_shares(self):
        s1 = calc_shares(1_000_000, 1.0, 1000, 980, lot_size=1)
        s2 = calc_shares(1_000_000, 2.0, 1000, 980, lot_size=1)
        assert s2 == s1 * 2

    def test_us_stock_lot_size_1(self):
        # 米国株 lot_size=1、shares_raw=500 → 500株
        shares = calc_shares(1_000_000, 1.0, 1000, 980, lot_size=1)
        assert shares == 500


class TestLotSizeForMarket:
    def test_jp_returns_100(self):
        assert lot_size_for_market("JP") == 100

    def test_us_returns_1(self):
        assert lot_size_for_market("US") == 1

    def test_unknown_defaults_to_1(self):
        assert lot_size_for_market("UNKNOWN") == 1


class TestHeat:
    def test_heat_is_risk_pct_times_positions(self):
        # 1% × 3ポジション = 3%
        assert heat(1_000_000, 1.0, 3) == pytest.approx(3.0)

    def test_zero_positions_is_zero_heat(self):
        assert heat(1_000_000, 1.0, 0) == pytest.approx(0.0)


class TestCalcPositionValue:
    def test_value_is_shares_times_price(self):
        assert calc_position_value(100, 1500.0) == pytest.approx(150_000.0)
