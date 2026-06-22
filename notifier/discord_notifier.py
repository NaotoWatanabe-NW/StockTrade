"""
Discord Webhook通知

事前準備:
  1. Discordサーバーの通知用チャンネルで「連携サービス」→「ウェブフック」作成
  2. WebhookのURLをコピー
  3. 環境変数 DISCORD_WEBHOOK_URL に設定

通知には「注文プラン（指値・損切り・利確）」「推奨株数」「保有銘柄の含み損益」を含める。
価格は各銘柄の市場通貨（¥ / $）で表記する。
"""

import logging
import requests
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _order_field(order, plan: dict, market) -> Optional[dict]:
    """注文プラン(OrderPlan) → Discord embed フィールド（SBI注文タイプ＋各レッグ）"""
    if order is None:
        return None
    lines = [leg.text(market) for leg in order.legs]
    followups = getattr(order, "followups", ()) or ()
    if followups:
        lines.append("─ 約定後に手動設定 ─")
        lines += [leg.text(market) for leg in followups]
    if plan:
        rr = []
        if plan.get("risk_pct") is not None:
            rr.append(f"リスク −{plan['risk_pct']:.1f}%")
        if plan.get("reward_pct") is not None:
            rr.append(f"リワード +{plan['reward_pct']:.1f}%")
        if rr:
            lines.append("（" + " / ".join(rr) + "）")
    if order.note:
        lines.append(f"※ {order.note}")
    return {
        "name": f"🧾 推奨注文：{order.order_type}",
        "value": "\n".join(lines),
        "inline": False,
    }


def _order_summary(order, market) -> str:
    """スクリーニング一覧用の1行サマリ"""
    if order is None:
        return ""
    legs = " / ".join(leg.text(market) for leg in order.legs)
    s = f"\n└ [{order.order_type}] {legs}"
    followups = getattr(order, "followups", ()) or ()
    if followups:
        fu = " / ".join(leg.text(market) for leg in followups)
        s += f"\n   └ 約定後: {fu}"
    return s


def _score_field(score) -> Optional[dict]:
    """合議制スコア(Consensus) → Discord embed フィールド（寄与上位を併記）"""
    if score is None:
        return None
    top = sorted(score.components, key=lambda c: abs(c.score * c.weight), reverse=True)[:3]
    detail = " / ".join(c.detail for c in top)
    return {
        "name": f"🧭 総合スコア {score.score:+.0f}（{score.jp_label}）",
        "value": detail or "—",
        "inline": False,
    }


def _score_summary(score) -> str:
    """スクリーニング一覧用のスコア1行"""
    if score is None:
        return ""
    return f"\n└ 🧭 {score.jp_label}（スコア {score.score:+.0f}）"


def _shares_summary(shares: Optional[int], market) -> str:
    """推奨株数のサマリ行（1行）"""
    if shares is None or shares == 0:
        return ""
    unit = "株" if market.code == "JP" else "株"
    return f"\n└ 📦 推奨株数: {shares:,}{unit}"


def _regime_summary(filters: dict) -> str:
    """レジームフィルタの通過状態を1行で表現"""
    if not filters:
        return ""
    icons = {
        "weekly_trend":  ("📈", "週足↑"),
        "index_regime":  ("🌐", "指数↑"),
        "adx":           ("📊", "ADX↑"),
    }
    parts = []
    for key, (icon, label) in icons.items():
        if key in filters:
            parts.append(f"{icon}{label}{'✓' if filters[key] else '✗'}")
    return "\n└ " + "  ".join(parts) if parts else ""


class DiscordNotifier:
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
        self.enabled = bool(webhook_url)
        if not self.enabled:
            log.warning("⚠️  DISCORD_WEBHOOK_URL未設定。通知はコンソール出力のみになります。")

    def _send(self, embed: dict):
        if not self.enabled:
            print(f"[Discord通知（未送信・URL未設定）] {embed.get('title')}")
            return
        try:
            res = requests.post(self.webhook_url, json={"embeds": [embed]}, timeout=10)
            res.raise_for_status()
        except Exception as e:
            log.error(f"Discord送信エラー: {e}")

    def test_connection(self) -> bool:
        """Webhookの疎通確認。テストメッセージを1通送り、成否を返す。"""
        if not self.enabled:
            log.error("DISCORD_WEBHOOK_URL が未設定です。.env か環境変数に設定してください。")
            return False
        try:
            res = requests.post(self.webhook_url, json={"embeds": [{
                "title": "✅ 接続テスト成功",
                "description": "株式監視ツールからのテスト通知です。これが表示されていれば設定完了です。",
                "color": 0x2ECC71,
                "timestamp": _now_iso(),
            }]}, timeout=10)
            res.raise_for_status()
            log.info("✅ Discordへのテスト通知に成功しました。")
            return True
        except Exception as e:
            log.error(f"❌ Discord接続テスト失敗: {e}")
            return False

    def notify_holding_signal(self, h: dict):
        """保有銘柄のシグナル通知（含み損益＋推奨株数＋注文プラン付き）"""
        market = h["market"]
        signals = h["signals"]
        labels = "、".join(s["label"] for s in signals)

        fields = [
            {"name": "現在値", "value": market.fmt(h["price"]), "inline": True},
            {"name": "前日比", "value": f"{h['change_pct']:+.1f}%", "inline": True},
        ]
        if h.get("avg_price"):
            fields.append({"name": "建値", "value": market.fmt(h["avg_price"]), "inline": True})
        if h.get("unrealized_pct") is not None:
            amt = h.get("unrealized_amount")
            pl = f"{h['unrealized_pct']:+.1f}%"
            if amt is not None:
                pl += f"（{market.fmt(amt)}）"
            fields.append({"name": "含み損益", "value": pl, "inline": True})
        if h.get("suggested_shares"):
            fields.append({
                "name": "推奨株数",
                "value": f"{h['suggested_shares']:,}株",
                "inline": True,
            })

        score_field = _score_field(h.get("score"))
        if score_field:
            fields.append(score_field)

        order_field = _order_field(h.get("order"), h.get("trade_plan"), market)
        if order_field:
            fields.append(order_field)

        title_prefix = "📌 長期保有・買い増し" if h.get("long_term") else "⚡ 保有銘柄シグナル"

        embed = {
            "title": f"{title_prefix}：{h['name']}（{h['code']}）",
            "description": labels,
            "color": 0xF1C40F,
            "fields": fields,
            "footer": {"text": "注文タイプ・価格は目安です。発注はSBI証券で手動でお願いします"},
            "timestamp": _now_iso(),
        }
        self._send(embed)

    def notify_screening_result(self, results: list[dict]):
        """スクリーニング結果をまとめて通知（推奨株数・レジーム状態を追記）"""
        if not results:
            self._send({
                "title": "📋 本日のスクリーニング結果",
                "description": "条件に合致する銘柄はありませんでした。",
                "color": 0x95A5A6,
                "timestamp": _now_iso(),
            })
            return

        lines = []
        for r in results[:15]:  # Discord embed 文字数制限を考慮
            market = r["market"]
            line = (
                f"**[{market.code}] {r['name']}（{r['code']}）** "
                f"{market.fmt(r['price'])} ({r['change_pct']:+.1f}%)\n"
                f"└ {'、'.join(s['label'] for s in r['signals'])}"
            )
            line += _score_summary(r.get("score"))
            line += _shares_summary(r.get("suggested_shares"), market)
            line += _regime_summary(r.get("filters", {}))
            line += _order_summary(r.get("order"), market)
            lines.append(line)

        self._send({
            "title": f"📋 本日のスクリーニング結果（{len(results)}銘柄）",
            "description": "\n\n".join(lines),
            "color": 0x3498DB,
            "footer": {"text": "注文タイプはATR基準の目安。発注は各自アプリで手動実施してください"},
            "timestamp": _now_iso(),
        })

    def notify_error(self, message: str):
        self._send({
            "title": "⚠️ エラー発生",
            "description": message,
            "color": 0xE74C3C,
            "timestamp": _now_iso(),
        })

    def notify_startup(self, holdings_count: int, universe_count: int,
                       risk_config: Optional[dict] = None):
        """起動通知。risk_config が渡されれば口座情報・最大ポジション数も表示する。"""
        desc_lines = [
            f"保有銘柄監視: {holdings_count}銘柄",
            f"スクリーニング対象: {universe_count}銘柄",
        ]
        if risk_config:
            account  = risk_config.get("account_size", 0)
            risk_pct = risk_config.get("risk_per_trade_pct", 1.0)
            max_pos  = risk_config.get("max_positions", 5)
            heat_now = holdings_count * risk_pct
            desc_lines += [
                f"口座サイズ: ¥{account:,.0f}",
                f"1トレードリスク: {risk_pct}%  最大保有: {max_pos}件",
                f"現在のポートフォリオ熱量: {heat_now:.1f}% / {max_pos * risk_pct:.1f}%",
            ]
        self._send({
            "title": "🚀 株式監視ツール起動",
            "description": "\n".join(desc_lines),
            "color": 0x2ECC71,
            "timestamp": _now_iso(),
        })
