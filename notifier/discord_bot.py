"""
Discord Bot（双方向）— 通知のリアクションでシグナル状態を更新する雛形

Webhook は送信専用なので、リアクション（スタンプ）を受け取るには Bot（gateway接続）が要る。
本モジュールは「リアクション→既存 API 呼び出し」のディスパッチを純粋関数として分離し、
discord.py 常駐部（run）はトークンのある環境で動かす。

依存: discord.py（任意・未インストールでも本体・テストは動く）
  pip install discord.py
環境変数:
  DISCORD_BOT_TOKEN … Bot トークン（必須）
  API_BASE          … バックエンドURL（既定 http://localhost:8000）

リアクション規約:
  ✅ … 約定を記録（推奨株数 × 計画指値で POST /api/signals/{id}/fill）
  ❌ … 見送り（POST /api/signals/{id}/status status=SKIPPED）

起動: python -m notifier.discord_bot
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Optional

import requests

log = logging.getLogger(__name__)

FILL_EMOJI = "✅"
SKIP_EMOJI = "❌"


def reaction_to_action(emoji: str) -> Optional[str]:
    """絵文字 → アクション（"fill" / "skip" / None）。"""
    if emoji == FILL_EMOJI:
        return "fill"
    if emoji == SKIP_EMOJI:
        return "skip"
    return None


def build_fill_payload(signal: dict, shares: int, traded_at: Optional[str] = None) -> dict:
    """fill API のリクエストボディ。価格は計画指値 entry_price を使う。"""
    return {
        "shares": shares,
        "price": signal.get("entry_price"),
        "traded_at": traded_at or date.today().isoformat(),
    }


def apply_reaction(signal: dict, action: str, *, api_base: str,
                   shares: int = 0, post=None) -> Optional[dict]:
    """アクションに応じて既存 API を叩く。テストのため post を注入可能。

    戻り値: 実行した内容の概要 dict（何もしない場合 None）。
    """
    post = post or requests.post
    sid = signal["id"]
    if action == "skip":
        res = post(f"{api_base}/api/signals/{sid}/status",
                   json={"status": "SKIPPED"}, timeout=10)
    elif action == "fill":
        if shares <= 0 or signal.get("entry_price") is None:
            log.warning("約定記録に必要な株数/指値が不明のためスキップ: signal=%s", sid)
            return None
        res = post(f"{api_base}/api/signals/{sid}/fill",
                   json=build_fill_payload(signal, shares), timeout=10)
    else:
        return None
    res.raise_for_status()
    return {"action": action, "signal_id": sid, "shares": shares if action == "fill" else None}


def _recommended_shares(api_base: str, signal_id: int) -> int:
    """推奨サイジングから該当シグナルの株数を取得する（無ければ 0）。"""
    try:
        r = requests.get(f"{api_base}/api/portfolio/suggestions", timeout=10)
        r.raise_for_status()
        for s in r.json().get("suggestions", []):
            if s["signal_id"] == signal_id:
                return int(s["suggested_shares"])
    except Exception as e:  # noqa: BLE001
        log.warning("推奨株数の取得に失敗: %s", e)
    return 0


def run() -> int:
    """discord.py 常駐部。リアクションを購読して既存 API を叩く。"""
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    api_base = os.environ.get("API_BASE", "http://localhost:8000")
    if not token:
        log.error("DISCORD_BOT_TOKEN が未設定です。.env か環境変数に設定してください。")
        return 1
    try:
        import discord
    except ImportError:
        log.error("discord.py が未インストールです。pip install discord.py を実行してください。")
        return 1

    intents = discord.Intents.default()
    intents.reactions = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_raw_reaction_add(payload):  # pragma: no cover - gateway 実機部
        action = reaction_to_action(str(payload.emoji))
        if action is None:
            return
        try:
            sig = requests.get(
                f"{api_base}/api/signals/by-message/{payload.message_id}", timeout=10
            )
            if sig.status_code == 404:
                return
            sig.raise_for_status()
            signal = sig.json()
            shares = _recommended_shares(api_base, signal["id"]) if action == "fill" else 0
            apply_reaction(signal, action, api_base=api_base, shares=shares)
            log.info("リアクション処理: %s signal=%s", action, signal["id"])
        except Exception as e:  # noqa: BLE001
            log.error("リアクション処理失敗: %s", e)

    log.info("Discord Bot を起動します（API=%s）", api_base)
    client.run(token)
    return 0


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(run())
