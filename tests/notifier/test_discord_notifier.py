"""notifier.discord_notifier のテスト（HTTP送信は行わない）"""

from unittest.mock import MagicMock, patch

import pytest

from notifier.discord_notifier import (
    DiscordNotifier,
    _regime_summary,
    _shares_summary,
)


# ──────────────────────────────────────────────────────────
# _shares_summary
# ──────────────────────────────────────────────────────────

class TestSharesSummary:
    def _market(self, code="JP"):
        m = MagicMock()
        m.code = code
        return m

    def test_shows_shares_when_positive(self):
        s = _shares_summary(200, self._market("JP"))
        assert "200" in s

    def test_empty_when_none(self):
        assert _shares_summary(None, self._market()) == ""

    def test_empty_when_zero(self):
        assert _shares_summary(0, self._market()) == ""


# ──────────────────────────────────────────────────────────
# _regime_summary
# ──────────────────────────────────────────────────────────

class TestRegimeSummary:
    def test_shows_all_passed(self):
        s = _regime_summary({"weekly_trend": True, "index_regime": True, "adx": True})
        assert "✓" in s
        assert "✗" not in s

    def test_shows_failed_filter(self):
        s = _regime_summary({"weekly_trend": False, "index_regime": True, "adx": True})
        assert "✗" in s

    def test_empty_when_no_filters(self):
        assert _regime_summary({}) == ""

    def test_empty_when_none_filters(self):
        assert _regime_summary({}) == ""


# ──────────────────────────────────────────────────────────
# DiscordNotifier（HTTP 送信を mock）
# ──────────────────────────────────────────────────────────

class TestDiscordNotifier:
    def _notifier(self):
        return DiscordNotifier("https://discord.com/api/webhooks/test/token")

    def _market(self, code="JP"):
        m = MagicMock()
        m.code = code
        m.fmt = lambda v: f"¥{v:,.0f}"
        return m

    @patch("notifier.discord_notifier.requests.post")
    def test_notify_startup_without_risk_config(self, mock_post):
        mock_post.return_value.raise_for_status = MagicMock()
        n = self._notifier()
        n.notify_startup(3, 50)
        mock_post.assert_called_once()
        body = mock_post.call_args[1]["json"]["embeds"][0]
        assert "3銘柄" in body["description"]
        assert "50銘柄" in body["description"]

    @patch("notifier.discord_notifier.requests.post")
    def test_notify_startup_with_risk_config(self, mock_post):
        mock_post.return_value.raise_for_status = MagicMock()
        n = self._notifier()
        n.notify_startup(2, 50, risk_config={
            "account_size": 1_000_000, "risk_per_trade_pct": 1.0, "max_positions": 5,
        })
        body = mock_post.call_args[1]["json"]["embeds"][0]
        assert "¥1,000,000" in body["description"]
        assert "熱量" in body["description"]

    @patch("notifier.discord_notifier.requests.post")
    def test_notify_screening_result_includes_shares(self, mock_post):
        mock_post.return_value.raise_for_status = MagicMock()
        n = self._notifier()
        market = self._market("JP")

        order = MagicMock()
        order.order_type = "IFDOCO"
        order.legs = []
        order.note = None

        n.notify_screening_result([{
            "code": "7203",
            "name": "トヨタ",
            "market": market,
            "price": 3000.0,
            "change_pct": 1.5,
            "signals": [{"label": "🟢 ゴールデンクロス"}],
            "score": None,
            "suggested_shares": 300,
            "filters": {"weekly_trend": True, "index_regime": True, "adx": True},
            "order": order,
            "trade_plan": None,
        }])
        body = mock_post.call_args[1]["json"]["embeds"][0]
        assert "300" in body["description"]   # suggested_shares が含まれる

    @patch("notifier.discord_notifier.requests.post")
    def test_notify_screening_empty_sends_no_results_message(self, mock_post):
        mock_post.return_value.raise_for_status = MagicMock()
        n = self._notifier()
        n.notify_screening_result([])
        body = mock_post.call_args[1]["json"]["embeds"][0]
        assert "ありませんでした" in body["description"]

    def test_no_send_when_url_empty(self, capsys):
        n = DiscordNotifier("")
        n.notify_error("test")
        captured = capsys.readouterr()
        assert "Discord通知" in captured.out
