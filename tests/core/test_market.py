"""core.market（市場判定・ティッカー整形・通貨表記）のテスト"""

import pytest

from core.market import JP, US, Market, resolve_market


class TestResolveMarket:
    def test_numeric_code_resolves_to_japan(self):
        assert resolve_market("7203") is JP

    def test_alphabetic_ticker_resolves_to_us(self):
        assert resolve_market("AAPL") is US

    def test_ticker_with_t_suffix_resolves_to_japan(self):
        assert resolve_market("7203.T") is JP

    def test_us_ticker_with_hyphen_resolves_to_us(self):
        # BRK-B は英字を含むため米国扱い
        assert resolve_market("BRK-B") is US

    def test_lowercase_ticker_resolves_to_us(self):
        assert resolve_market("aapl") is US

    def test_explicit_market_overrides_autodetection(self):
        # 数字コードでも明示指定があればそちらを優先
        assert resolve_market("7203", explicit="US") is US

    def test_explicit_market_is_case_insensitive(self):
        assert resolve_market("AAPL", explicit="jp") is JP

    def test_unknown_explicit_market_raises_value_error(self):
        with pytest.raises(ValueError):
            resolve_market("7203", explicit="FR")


class TestMarketTicker:
    def test_japan_appends_t_suffix(self):
        assert JP.ticker("7203") == "7203.T"

    def test_japan_does_not_double_append_suffix(self):
        assert JP.ticker("7203.T") == "7203.T"

    def test_us_keeps_ticker_unchanged(self):
        assert US.ticker("AAPL") == "AAPL"

    def test_ticker_is_uppercased(self):
        assert US.ticker("aapl") == "AAPL"
        assert JP.ticker("7203.t") == "7203.T"


class TestMarketFmt:
    def test_yen_is_formatted_without_decimals(self):
        assert JP.fmt(2800) == "¥2,800"

    def test_yen_rounds_to_integer(self):
        assert JP.fmt(2799.6) == "¥2,800"

    def test_dollar_is_formatted_with_two_decimals(self):
        assert US.fmt(180.5) == "$180.50"

    def test_thousands_separator_is_applied(self):
        assert US.fmt(1234567.5) == "$1,234,567.50"

    def test_none_value_renders_dash(self):
        assert JP.fmt(None) == "-"
        assert US.fmt(None) == "-"


def test_market_is_immutable():
    # frozen dataclass のため属性変更は不可
    with pytest.raises(Exception):
        JP.currency = "$"  # type: ignore[misc]
