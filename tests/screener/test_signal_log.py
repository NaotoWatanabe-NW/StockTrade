"""screener.signal_log（スキャン結果→signalsテーブル永続化）のテスト"""

import pytest

from core.market import JP
from data.db import get_connection
from data.repository import list_signals, save_signal, get_signal_by_message_id
from screener.signal_log import record_scan_signals, notify_and_link_signal


@pytest.fixture
def conn():
    c = get_connection(":memory:")
    yield c
    c.close()


def _result(code="7011", side="BUY", with_plan=True):
    """scan_universe() が返す形を最小限で模した結果dict"""
    plan = None
    if with_plan:
        plan = {
            "side": side, "entry_kind": "LIMIT",
            "entry": 1000.0, "stop": 950.0, "target": 1150.0,
        }
    # consensus / order は属性アクセスされるので簡易オブジェクトで代用
    consensus = type("C", (), {"score": 42.0})()
    order = type("O", (), {"order_type": "IFDOCO"})()
    return {
        "code": code, "name": "三菱重工", "market": JP,
        "price": 1000.0, "change_pct": 1.2,
        "signals": [{"type": "BREAKOUT_HIGH"}, {"type": "MA_GOLDEN_CROSS"}],
        "score": consensus, "trade_plan": plan,
        "order": order,
    }


def test_records_buy_signal_with_plan(conn):
    saved = record_scan_signals(conn, [_result()])
    assert saved == 1
    sigs = list_signals(conn)
    assert len(sigs) == 1
    s = sigs[0]
    assert s["code"] == "7011"
    assert s["market"] == "JP"
    assert s["risk"] == 50.0
    assert s["score"] == 42.0
    assert s["order_type"] == "IFDOCO"
    assert s["status"] == "OPEN"


def test_skips_result_without_plan(conn):
    # 方向性のない（trade_plan が None）シグナルは記録しない
    saved = record_scan_signals(conn, [_result(with_plan=False)])
    assert saved == 0
    assert list_signals(conn) == []


def test_dedup_same_code_side_same_day(conn):
    record_scan_signals(conn, [_result()])
    saved = record_scan_signals(conn, [_result()])  # 同日同方向 → 二重記録しない
    assert saved == 0
    assert len(list_signals(conn)) == 1


def test_signal_types_serialized(conn):
    record_scan_signals(conn, [_result()])
    s = list_signals(conn)[0]
    assert '"BREAKOUT_HIGH"' in s["signal_types"]
    assert '"MA_GOLDEN_CROSS"' in s["signal_types"]


class _StubNotifier:
    """send_signal_card が固定の message_id を返すスタブ。"""
    def __init__(self, message_id):
        self.message_id = message_id
        self.sent = []

    def send_signal_card(self, signal):
        self.sent.append(signal)
        return self.message_id


def test_notify_and_link_stores_message_id(conn):
    sig = save_signal(conn, code="7011", side="BUY", entry_price=1000, stop_price=950)
    mid = notify_and_link_signal(conn, _StubNotifier("msg-123"), sig)
    assert mid == "msg-123"
    # メッセージID から逆引きできる
    found = get_signal_by_message_id(conn, "msg-123")
    assert found is not None and found["id"] == sig["id"]


def test_notify_and_link_skips_storage_when_not_sent(conn):
    sig = save_signal(conn, code="7011", side="BUY", entry_price=1000, stop_price=950)
    mid = notify_and_link_signal(conn, _StubNotifier(None), sig)  # 送信不可
    assert mid is None
    assert get_signal_by_message_id(conn, "anything") is None
