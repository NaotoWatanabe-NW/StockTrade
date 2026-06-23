"""寝ている保有資産（値動きの乏しい銘柄）を検出して Discord 通知するツール。

スイング取引では「1日の値動き（ATR）が小さい銘柄」は値幅を取れず資金が寝る。
このツールは保有銘柄のうち、長期保有フラグ(long_term)を除いた上で、
ATR%/日が閾値未満の銘柄を「寝ている資産」として抽出し、戻り売り指値・撤退
逆指値（core.trade_plan の SELL ロジック）を付けて Discord に通知する。

発注は手動（SBIにAPIなし）。指値=条件なし／逆指値=成行。

実行:
    .venv/bin/python -m scripts.notify_idle_holdings              # 通知を送信
    .venv/bin/python -m scripts.notify_idle_holdings --dry-run    # 送信せず内容を表示
    .venv/bin/python -m scripts.notify_idle_holdings --atr-max 1.5  # 閾値変更

長期保有フラグを無視して全保有を対象にしたい場合は --include-long-term。
"""

import sys
import argparse
import logging
import warnings
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

warnings.filterwarnings("ignore")

from core.data_client import StockDataClient
from core.indicators import add_technical_indicators
from core.market import resolve_market
from core.trade_plan import build_trade_plan
from config import SCREENING_CONFIG, TRADE_PLAN_CONFIG, DISCORD_WEBHOOK_URL
from data.db import get_connection
from data.repository import list_holdings
from notifier.discord_notifier import DiscordNotifier

log = logging.getLogger(__name__)

# 「寝ている」と判定する 1日あたり ATR の閾値（% / 日）。
# これ未満は平均的な日中値幅が小さく、スイングで値幅を取りにくい。
DEFAULT_ATR_PCT_MAX = 2.0


def find_idle_holdings(client: StockDataClient, atr_pct_max: float,
                       include_long_term: bool) -> list[dict]:
    """寝ている保有銘柄を抽出し、売却プラン付きで返す（ATR%/日の昇順）。"""
    conn = get_connection()
    try:
        holdings = list_holdings(conn)
    finally:
        conn.close()

    idle = []
    for h in holdings:
        # 長期保有フラグ・実保有なしは対象外
        if not include_long_term and h.get("long_term"):
            continue
        if not h.get("shares") or h["shares"] <= 0:
            continue

        market = resolve_market(h["code"])
        df = client.get_history(h["code"], period="1y", interval="1d")
        if df is None or len(df) < 60:
            log.warning("データ不足のためスキップ: %s", h["code"])
            continue

        df = add_technical_indicators(df, SCREENING_CONFIG)
        cur = float(df["close"].iloc[-1])
        atr = float(df["atr"].iloc[-1])
        if cur <= 0 or atr <= 0:
            continue
        atr_pct = atr / cur * 100
        if atr_pct >= atr_pct_max:
            continue

        plan = build_trade_plan("SELL", cur, atr, TRADE_PLAN_CONFIG)
        if plan is None:
            continue
        pnl = (cur / h["avg_price"] - 1) * 100 if h.get("avg_price") else None

        idle.append({
            "code": h["code"], "name": h["name"], "market": market,
            "shares": h["shares"], "price": cur, "atr_pct": atr_pct,
            "pnl_pct": pnl, "limit": plan["entry"], "stop": plan["stop"],
        })

    idle.sort(key=lambda c: c["atr_pct"])
    return idle


def _build_embed(idle: list[dict], atr_pct_max: float) -> dict:
    fields = []
    for c in idle:
        mkt = c["market"]
        pnl_s = "—" if c["pnl_pct"] is None else f"{c['pnl_pct']:+.1f}%"
        fields.append({
            "name": f"{c['code']} {c['name']}",
            "value": (
                f"現在値 {mkt.fmt(c['price'])} ／ 含み損益 {pnl_s} ／ "
                f"値動き(ATR) {c['atr_pct']:.2f}%/日\n"
                f"🔻 戻り売り指値: **{mkt.fmt(c['limit'])}**"
                f"（{c['shares']:.0f}株・指値/条件なし）\n"
                f"🛑 撤退逆指値: {mkt.fmt(c['stop'])}（成行・割れたら損切り）"
            ),
            "inline": False,
        })
    return {
        "title": "💤 寝ている資産の売却プラン（スイング資金化）",
        "description": (
            "長期保有フラグ(long_term)を除いた保有のうち、1日の値動き(ATR)が"
            f"{atr_pct_max:.1f}%未満で値動きが乏しい銘柄。戻り売り指値で手仕舞い候補。\n"
            "※SBIにAPIはないため発注は手動。指値=条件なし／逆指値=成行。"
        ),
        "color": 0xF1C40F,
        "fields": fields,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "StockTrade 分析ツール｜投資助言ではありません"},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="寝ている保有資産を検出してDiscord通知")
    parser.add_argument("--atr-max", type=float, default=DEFAULT_ATR_PCT_MAX,
                        help=f"寝ている判定のATR%%/日の上限（既定 {DEFAULT_ATR_PCT_MAX}）")
    parser.add_argument("--include-long-term", action="store_true",
                        help="長期保有フラグの銘柄も対象に含める")
    parser.add_argument("--dry-run", action="store_true",
                        help="Discordに送らずコンソールに内容を表示する")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    client = StockDataClient(rate_limit_sec=0.6)
    idle = find_idle_holdings(client, args.atr_max, args.include_long_term)

    if not idle:
        print(f"寝ている資産は見つかりませんでした（ATR%/日 < {args.atr_max}）。通知はスキップします。")
        return 0

    print(f"寝ている資産 {len(idle)} 件（ATR%/日 < {args.atr_max}）:")
    for c in idle:
        print(f"  {c['code']:6} {c['name'][:12]:14} ATR {c['atr_pct']:.2f}%/日  "
              f"指値 {c['market'].fmt(c['limit'])}")

    embed = _build_embed(idle, args.atr_max)
    if args.dry_run:
        print("\n[--dry-run] Discordには送信していません。")
        return 0

    DiscordNotifier(DISCORD_WEBHOOK_URL)._send(embed)
    print(f"\nDiscordへ送信しました（{len(idle)}件）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
