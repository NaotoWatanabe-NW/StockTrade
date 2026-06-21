"""
バックテスト実行CLI

ユニバース全銘柄に対してバックテストを実行し、集計レポートを標準出力に出力する。

使い方:
    python -m backtest.runner                      # config.py のデフォルト設定で実行
    python -m backtest.runner --universe JP        # 日本株のみ
    python -m backtest.runner --universe US        # 米国株のみ
    python -m backtest.runner --min-score 30       # スコア絶対値 30 以上のシグナルのみ
    python -m backtest.runner --no-cache           # キャッシュを使わず yfinance から再取得

DBキャッシュ（stock.db の price_history テーブル）に ~5 年分の日足を保存しつつ走る。
初回実行は yfinance からの取得で時間がかかる（銘柄数 × 1〜2 秒程度）。
"""

from __future__ import annotations

import argparse
import logging
import sys

import config
from backtest.metrics import compute_metrics, format_report
from backtest.simulator import Trade, NoFill, simulate_symbol
from core.market import resolve_market
from data.db import get_connection
from data.price_cache import get_history_cached

log = logging.getLogger(__name__)


def _build_backtest_cfg(args, base: dict) -> dict:
    cfg = dict(base)
    if args.min_score is not None:
        cfg["min_abs_score"] = args.min_score
    return cfg


def run(args) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler()],
    )

    universe = config.SCREENING_UNIVERSE
    if args.universe == "JP":
        universe = config.SCREENING_UNIVERSE_JP
    elif args.universe == "US":
        universe = config.SCREENING_UNIVERSE_US

    screening_cfg   = config.SCREENING_CONFIG
    scoring_cfg     = config.SCORING_CONFIG
    trade_plan_cfg  = config.TRADE_PLAN_CONFIG
    backtest_cfg    = _build_backtest_cfg(args, config.BACKTEST_CONFIG)
    exit_cfg        = dict(config.EXIT_CONFIG)
    if args.no_partial_tp:
        exit_cfg["partial_tp_pct"] = 0.0
        exit_cfg["move_to_breakeven"] = False

    years = int(backtest_cfg.get("history", "5y").rstrip("y"))

    all_trades: list[Trade] = []
    all_no_fills: list[NoFill] = []

    conn = get_connection()
    try:
        for code in universe:
            market = resolve_market(code)
            log.info(f"処理中: {code} [{market.code}]")

            if args.no_cache:
                from core.data_client import StockDataClient
                client = StockDataClient(rate_limit_sec=1.0)
                df = client.get_history(code, market, period=f"{years}y", interval="1d")
            else:
                df = get_history_cached(conn, code, market, interval="1d", years=years)

            if df is None or df.empty:
                log.warning(f"  スキップ（データなし）: {code}")
                continue

            trades, no_fills = simulate_symbol(
                code, df, screening_cfg, scoring_cfg, trade_plan_cfg, backtest_cfg, exit_cfg
            )
            all_trades.extend(trades)
            all_no_fills.extend(no_fills)

            sym_metrics = compute_metrics(trades, no_fills)
            log.info(
                f"  {code}: signal={sym_metrics['total_signals']} "
                f"fill={sym_metrics['fill_rate']*100:.0f}% "
                f"win={sym_metrics['win_rate']*100:.0f}% "
                f"avgR={sym_metrics['avg_r']:+.2f}"
            )
    finally:
        conn.close()

    # ── 集計レポート ──────────────────────────────────────────────────
    total_metrics = compute_metrics(all_trades, all_no_fills)
    print("\n" + format_report(total_metrics, title="全銘柄集計"))

    # 銘柄別集計（シグナルが存在したものだけ）
    by_code: dict[str, tuple[list, list]] = {}
    for t in all_trades:
        by_code.setdefault(t.code, ([], []))[0].append(t)
    for nf in all_no_fills:
        by_code.setdefault(nf.code, ([], []))[1].append(nf)

    if by_code:
        print("\n── 銘柄別サマリー ──────────────────────────")
        header = f"{'コード':<8} {'シグナル':>8} {'約定率':>7} {'勝率':>7} {'avgR':>7} {'PF':>6}"
        print(header)
        print("-" * len(header))
        rows = []
        for code, (trs, nfs) in by_code.items():
            m = compute_metrics(trs, nfs)
            pf = m["profit_factor"]
            pf_str = f"{pf:.2f}" if pf != float("inf") else "∞"
            rows.append((
                m["avg_r"],
                f"{code:<8} {m['total_signals']:>8} "
                f"{m['fill_rate']*100:>6.0f}% "
                f"{m['win_rate']*100:>6.0f}% "
                f"{m['avg_r']:>+7.3f} "
                f"{pf_str:>6}",
            ))
        rows.sort(key=lambda x: x[0], reverse=True)
        for _, line in rows:
            print(line)

    return 0


def main():
    parser = argparse.ArgumentParser(description="スイング取引バックテスト")
    parser.add_argument("--universe", choices=["JP", "US", "ALL"], default="ALL")
    parser.add_argument("--min-score", type=float, default=None,
                        help="合議スコアの絶対値フィルタ（例: 30）")
    parser.add_argument("--no-cache", action="store_true",
                        help="DBキャッシュを使わず yfinance から再取得")
    parser.add_argument("--no-partial-tp", action="store_true",
                        help="部分利確なし（Phase 1 互換モード）")
    args = parser.parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
