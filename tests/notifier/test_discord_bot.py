"""notifier.discord_bot のリアクション→API ディスパッチのテスト（HTTPは注入で代替）"""

import pytest

from notifier.discord_bot import (
    reaction_to_action,
    build_fill_payload,
    apply_reaction,
    FILL_EMOJI,
    SKIP_EMOJI,
)


class _FakeResponse:
    def raise_for_status(self):
        return None


class _RecordingPost:
    """requests.post を代替し、呼び出し（url, json）を記録するスタブ。"""
    def __init__(self):
        self.calls = []

    def __call__(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json})
        return _FakeResponse()


class TestReactionToAction:
    def test_fill_emoji_maps_to_fill(self):
        assert reaction_to_action(FILL_EMOJI) == "fill"

    def test_skip_emoji_maps_to_skip(self):
        assert reaction_to_action(SKIP_EMOJI) == "skip"

    def test_unknown_emoji_maps_to_none(self):
        assert reaction_to_action("🎉") is None


class TestBuildFillPayload:
    def test_uses_entry_price_and_given_shares(self):
        p = build_fill_payload({"entry_price": 1520.0}, 100, traded_at="2026-06-25")
        assert p == {"shares": 100, "price": 1520.0, "traded_at": "2026-06-25"}


class TestApplyReaction:
    def test_skip_calls_status_endpoint(self):
        post = _RecordingPost()
        out = apply_reaction({"id": 7}, "skip", api_base="http://x", post=post)
        assert out["action"] == "skip"
        assert post.calls[0]["url"] == "http://x/api/signals/7/status"
        assert post.calls[0]["json"] == {"status": "SKIPPED"}

    def test_fill_calls_fill_endpoint_with_shares(self):
        post = _RecordingPost()
        signal = {"id": 7, "entry_price": 1000.0}
        out = apply_reaction(signal, "fill", api_base="http://x", shares=200, post=post)
        assert out["shares"] == 200
        assert post.calls[0]["url"] == "http://x/api/signals/7/fill"
        assert post.calls[0]["json"]["shares"] == 200
        assert post.calls[0]["json"]["price"] == 1000.0

    def test_fill_without_shares_does_nothing(self):
        post = _RecordingPost()
        out = apply_reaction({"id": 7, "entry_price": 1000.0}, "fill",
                             api_base="http://x", shares=0, post=post)
        assert out is None
        assert post.calls == []
