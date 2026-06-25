"""
スクリーナーのシグナルをDBに永続化する連携層

scan_universe() が返す結果dictを signals テーブルに記録し、
あとで実取引（trades）と紐付けてライブ成績をバックテスト期待値と
比較できるようにする（フィードバックループの入口）。

同一銘柄・同一方向の OPEN シグナルが当日すでにあれば二重記録しない。
発注プランの無いシグナル（NEUTRAL＝出来高急増のみ等）は記録しない。
"""

from __future__ import annotations

import logging

from data.repository import save_signal, exists_open_signal_today, set_signal_message_id

log = logging.getLogger(__name__)


def notify_and_link_signal(conn, notifier, signal: dict) -> str | None:
    """シグナルを個別通知し、返ってきた Discord メッセージID を signals に紐付ける。

    notifier は send_signal_card(signal)->message_id を持つもの（DiscordNotifier）。
    リアクション双方向（✅約定/❌見送り）のための紐付け。送信不可なら None。
    """
    msg_id = notifier.send_signal_card(signal)
    if msg_id:
        set_signal_message_id(conn, signal["id"], msg_id)
    return msg_id


def record_scan_signals(conn, results: list[dict]) -> int:
    """
    scan_universe() の結果リストを signals テーブルに記録する。

    戻り値: 新規に記録したシグナル件数。
    """
    saved = 0
    for r in results:
        plan = r.get("trade_plan")
        if not plan or not plan.get("side"):
            continue  # 方向性のないシグナルは記録対象外

        side = plan["side"]
        if exists_open_signal_today(conn, r["code"], side):
            continue  # 当日分の重複を抑止

        market = r.get("market")
        market_code = getattr(market, "code", None) or (market if isinstance(market, str) else None)
        consensus = r.get("score")
        order = r.get("order")

        save_signal(
            conn,
            code=r["code"],
            side=side,
            name=r.get("name"),
            market=market_code,
            signal_types=[s["type"] for s in r.get("signals", [])],
            score=getattr(consensus, "score", None),
            entry_price=plan.get("entry"),
            stop_price=plan.get("stop"),
            target_price=plan.get("target"),
            entry_kind=plan.get("entry_kind"),
            order_type=getattr(order, "order_type", None),
        )
        saved += 1

    if saved:
        log.info(f"シグナルを {saved} 件記録しました（signals テーブル）")
    return saved
