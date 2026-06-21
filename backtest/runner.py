"""
バックテスト実行CLI

ユニバース全銘柄に対してバックテストを実行し、集計レポートを標準出力に出力する。

使い方:
    python -m backtest.runner                      # config.py のデフォルト設定で実行
    python -m backtest.runner --universe JP        # 日本株のみ
    python -m backtest.runner --universe US        # 米国株のみ
    python -m backtest.runner --min-score 30       # スコア絶対値 30 以上のシグナルのみ
    python -m backtest.runner --no-cache           # キャッシュを使わず yfinance から再取得
    python -m backtest.runner --no-regime          # レジームフィルタを無効化

DBキャッシュ（stock.db の price_history テーブル）に ~5 年分の日足を保存しつつ走る。
初回実行は yfinance からの取得で時間がかかる（銘柄数 × 1〜2 秒程度）。
"""

from __future__ import annotations

import argparse
import logging
import sys

import config
from backtest.metrics import compute_metrics, format_report
from backtest.optimizer import run_walk_forward, format_wf_report
from backtest.simulator import Trade, NoFill, simulate_symbol
from core.market import resolve_market
from data.db import get_connection
from data.price_cache import get_history_cached

log = logging.getLogger(__name__)


def _run_optimize(
    args, universe, screening_cfg, scoring_cfg, trade_plan_cfg,
    backtest_cfg, exit_cfg, regime_cfg, years,
) -> int:
    """ウォークフォワード最適化を実行してレポートを出力する。"""
    optimize_cfg = config.OPTIMIZE_CONFIG

    log.info("全銘柄の価格データを一括ロード中...")
    conn = get_connection()
    universe_dfs: dict = {}
    weekly_dfs: dict = {}
    index_dfs: dict = {}

    try:
        for code in universe:
            market = resolve_market(code)
            df = get_history_cached(conn, code, market, interval="1d", years=years)
            if df is not None and not df.empty:
                universe_dfs[code] = df
            else:
                log.warning(f"  スキップ（データなし）: {code}")

            if regime_cfg and regime_cfg.get("weekly_trend_filter", True):
                df_wk = get_history_cached(conn, code, market, interval="1wk", years=years)
                if df_wk is not None and not df_wk.empty:
                    weekly_dfs[code] = df_wk

        if regime_cfg:
            _fetch_index_df(regime_cfg, universe, index_dfs)
    finally:
        conn.close()

    log.info(f"データロード完了: {len(universe_dfs)} 銘柄")
    log.info("ウォークフォワード最適化を開始します...")

    result = run_walk_forward(
        universe_dfs=universe_dfs,
        screening_cfg=screening_cfg,
        scoring_cfg=scoring_cfg,
        trade_plan_cfg=trade_plan_cfg,
        backtest_cfg=backtest_cfg,
        exit_cfg=exit_cfg,
        optimize_cfg=optimize_cfg,
        regime_cfg=regime_cfg,
        weekly_dfs=weekly_dfs if weekly_dfs else None,
        index_dfs=index_dfs if index_dfs else None,
    )

    print(format_wf_report(result))
    return 0


def _fetch_index_df(regime_cfg: dict, universe: list, index_by_market: dict) -> None:
    """指数日足データを market 単位で取得してキャッシュ辞書に格納する。"""
    import yfinance as yf
    from core.market import resolve_market as _rm
    markets_in_universe = {_rm(c).code for c in universe}
    for mkt_code in markets_in_universe:
        idx_code = regime_cfg.get("jp_index", "^N225") if mkt_code == "JP" else regime_cfg.get("us_index", "^GSPC")
        try:
            log.info(f"指数データ取得中: {idx_code}")
            df = yf.Ticker(idx_code).history(period="7y", interval="1d")
            if df.empty:
                log.warning(f"  指数データなし: {idx_code}")
                continue
            df.columns = [c.lower() for c in df.columns]
            # timezone 情報を除去して date-only インデックスに統一
            df.index = df.index.tz_localize(None) if df.index.tz is not None else df.index
            index_by_market[mkt_code] = df
            log.info(f"  取得完了: {idx_code} ({len(df)} bars)")
        except Exception as e:
            log.warning(f"  指数取得失敗 {idx_code}: {e}")


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

    regime_cfg = None if args.no_regime else config.REGIME_CONFIG

    years = int(backtest_cfg.get("history", "5y").rstrip("y"))

    # ──────────────────────────────────────────
    # ウォークフォワード最適化モード
    # ──────────────────────────────────────────
    if args.optimize:
        return _run_optimize(args, universe, screening_cfg, scoring_cfg, trade_plan_cfg,
                             backtest_cfg, exit_cfg, regime_cfg, years)

    all_trades: list[Trade] = []
    all_no_fills: list[NoFill] = []

    # レジームフィルタ有効時は指数データをスキャン開始前に1回取得
    index_by_market: dict[str, object] = {}
    if regime_cfg:
        _fetch_index_df(regime_cfg, universe, index_by_market)

    conn = get_connection()
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

        # 週足データ（レジームフィルタ有効時のみ）
        df_weekly = None
        if regime_cfg and regime_cfg.get("weekly_trend_filter", True):
            df_weekly = get_history_cached(conn, code, market, interval="1wk", years=years)

        trades, no_fills = simulate_symbol(
            code, df, screening_cfg, scoring_cfg, trade_plan_cfg, backtest_cfg, exit_cfg,
            regime_cfg=regime_cfg,
            df_weekly=df_weekly,
            df_index=index_by_market.get(market.code),
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

    # ── 集計レポート ──────────────────────────────────────────────────
    total_metrics = compute_metrics(all_trades, all_no_fills, risk_cfg=config.RISK_CONFIG)
    print("\n" + format_report(total_metrics, title="全銘柄集計"))

    # --save フラグで DB に保存
    if args.save:
        from data.repository import save_backtest_run
        params_snapshot = {
            "exit_cfg":     exit_cfg,
            "backtest_cfg": backtest_cfg,
            "regime":       bool(regime_cfg),
        }
        run_id = save_backtest_run(conn, args.universe, total_metrics, params_snapshot)
        log.info(f"バックテスト結果を保存しました（id={run_id}）")

    conn.close()

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
    parser.add_argument("--no-regime", action="store_true",
                        help="レジームフィルタを無効化して Phase 2 と同条件で比較")
    parser.add_argument("--optimize", action="store_true",
                        help="ウォークフォワード最適化を実行（通常バックテストの代わりに）")
    parser.add_argument("--save", action="store_true",
                        help="バックテスト結果を stock.db の backtest_runs テーブルに保存")
    args = parser.parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
