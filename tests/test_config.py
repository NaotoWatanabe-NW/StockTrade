"""config の実行時マージ（_effective_section）のテスト

DB に触れず、上書き dict を明示的に渡してマージ挙動を検証する。
"""

import config


class TestEffectiveSection:
    def test_applies_only_params_targeting_that_section(self):
        eff = config._effective_section(
            "SCREENING_CONFIG",
            {"breakout_lookback": 99, "trail_atr_mult": 9.0},  # 後者は EXIT 宛
        )
        assert eff["breakout_lookback"] == 99       # SCREENING 宛は反映
        assert "trail_atr_mult" not in eff           # EXIT 宛は混入しない

    def test_preserves_other_defaults(self):
        eff = config._effective_section("SCREENING_CONFIG", {"breakout_lookback": 99})
        assert eff["ma_short"] == config.SCREENING_CONFIG["ma_short"]

    def test_no_overrides_returns_defaults_copy(self):
        eff = config._effective_section("RISK_CONFIG", {})
        assert eff == config.RISK_CONFIG
        assert eff is not config.RISK_CONFIG          # コピー（グローバルを壊さない）

    def test_account_size_override_routes_to_risk_config(self):
        eff = config._effective_section("RISK_CONFIG", {"account_size": 2_000_000})
        assert eff["account_size"] == 2_000_000
