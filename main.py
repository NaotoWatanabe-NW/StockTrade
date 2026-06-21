"""
株式監視・スクリーニングツール メイン実行スクリプト

役割:
  1. 保有銘柄の売買シグナルを監視 → 指値・損切り・利確プラン付きでDiscord通知
  2. スクリーニングユニバース（日本＋米国）から新規候補を探索 → Discord通知

⚠️  発注は行いません。シグナル確認後、SBI証券アプリ等で手動発注してください。

使い方:
    export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
    python main.py              # 1回だけ実行（全市場をスキャン）
    python main.py --loop       # 定期実行（開いている市場のみスキャン）
"""

import time
import logging
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo

import config
from core.data_client import StockDataClient
from core.market import JP, US, resolve_market
from core.trade_plan import net_side
from screener.engine import StockScreener
from notifier.discord_notifier import DiscordNotifier

# ── ロギング設定 ───────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("stock_tool.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


def open_markets() -> set[str]:
    """いま取引時間内の市場コードの集合を返す（{"JP","US"} の部分集合）"""
    out = set()
    for m in (JP, US):
        now = datetime.now(ZoneInfo(m.tz))
        if now.weekday() < 5 and m.open <= now.strftime("%H:%M") <= m.close:
            out.add(m.code)
    return out


def _filter_by_market(codes, markets: set[str] | None):
    """markets=None なら全件、指定があればその市場の銘柄のみ"""
    if markets is None:
        return list(codes)
    return [c for c in codes if resolve_market(c).code in markets]


def _filter_holdings(holdings, markets: set[str] | None):
    if markets is None:
        return list(holdings)
    return [h for h in holdings if resolve_market(h["code"], h.get("market")).code in markets]


def should_notify_holding(result: dict, notify_cfg: dict) -> bool:
    """保有銘柄シグナルを通知すべきか判定する。

    - シグナルなし → 通知しない
    - 方向性のない（NEUTRAL＝出来高急増のみ等）→ suppress_neutral_holdings なら通知しない
    - 長期保有(long_term) かつ 売り方向 → 通知しない（買い増しタイミングのみ通知）
    """
    signals = result["signals"]
    if not signals:
        return False
    side = net_side(signals)
    if side == "NEUTRAL" and notify_cfg.get("suppress_neutral_holdings", True):
        return False
    if result.get("long_term") and side == "SELL":
        return False
    return True


def run_once(screener: StockScreener, notifier: DiscordNotifier, markets: set[str] | None = None):
    """1回分のチェックサイクル。markets指定時はその市場の銘柄のみ対象"""
    log.info("=" * 50)
    log.info(f"📊 チェック開始（対象市場: {markets or 'ALL'}）")

    # ── 1. 保有銘柄チェック（DBから最新を取得） ─────────
    all_holdings = config.get_holdings()
    holdings = _filter_holdings(all_holdings, markets)
    if holdings:
        log.info(f"保有銘柄チェック中（{len(holdings)}銘柄）...")
        for r in screener.check_holdings(holdings):
            if should_notify_holding(r, config.NOTIFY_CONFIG):
                notifier.notify_holding_signal(r)
            if r.get("unrealized_pct") is not None:
                log.info(f"  {r['name']}: 含み損益 {r['unrealized_pct']:+.1f}%")
    elif all_holdings:
        log.info("対象市場の保有銘柄はありません")
    else:
        log.info("保有銘柄が未登録です（Webアプリ、または scripts/migrate_holdings で登録）")

    # ── 2. スクリーニング ─────────────────────────────
    universe = _filter_by_market(config.SCREENING_UNIVERSE, markets)
    log.info(f"スクリーニング中（{len(universe)}銘柄）...")
    results = screener.scan_universe(universe)
    log.info(f"✅ {len(results)}銘柄でシグナル検出")
    notifier.notify_screening_result(results)

    log.info("📊 チェック完了\n")


def main():
    parser = argparse.ArgumentParser(description="株式監視・スクリーニングツール")
    parser.add_argument("--loop", action="store_true", help="定期実行モード")
    parser.add_argument(
        "--interval", type=int,
        default=config.SIGNAL_CONFIG["check_interval_minutes"],
        help="チェック間隔（分）",
    )
    parser.add_argument(
        "--test-notify", action="store_true",
        help="Discord Webhookの疎通確認だけ行って終了",
    )
    args = parser.parse_args()

    notifier = DiscordNotifier(config.DISCORD_WEBHOOK_URL)

    # ── Discord接続テスト（疎通確認のみ） ─────────────
    if args.test_notify:
        ok = notifier.test_connection()
        raise SystemExit(0 if ok else 1)

    client   = StockDataClient(rate_limit_sec=1.0)
    screener = StockScreener(client, config.SCREENING_CONFIG,
                             config.TRADE_PLAN_CONFIG, config.ORDER_CONFIG,
                             config.SCORING_CONFIG)

    notifier.notify_startup(len(config.get_holdings()), len(config.SCREENING_UNIVERSE))

    if not args.loop:
        run_once(screener, notifier)  # 単発は全市場をスキャン
        return

    log.info(f"🔁 定期実行モード開始（{args.interval}分間隔・開いている市場のみ）")
    while True:
        try:
            markets = open_markets()
            if markets:
                run_once(screener, notifier, markets)
            else:
                log.info("⏸ 日本・米国とも市場時間外のためスキップ")
        except KeyboardInterrupt:
            log.info("⏹ 停止しました")
            break
        except Exception as e:
            log.error(f"❌ エラー: {e}")
            notifier.notify_error(str(e))

        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
