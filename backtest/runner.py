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
from typing import Optional

import config
from backtest.metrics import compute_metrics, format_report
from backtest.optimizer import run_walk_forward, format_wf_report
from backtest.simulator import Trade, NoFill, simulate_symbol
from core.market import resolve_market
from core.sector import build_sector_indices, sector_series_for
from data.db import get_connection
from data.price_cache import get_history_cached

log = logging.getLogger(__name__)


def _run_optimize(
    args, universe, screening_cfg, scoring_cfg, trade_plan_cfg,
    backtest_cfg, exit_cfg, regime_cfg, years, sector_cfg=None,
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

        code_to_group, sector_indices = _build_sector_context(conn, universe_dfs, sector_cfg)
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
        sector_cfg=sector_cfg,
        code_to_group=code_to_group if code_to_group else None,
        sector_indices=sector_indices if sector_indices else None,
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


def _build_sector_context(conn, universe_dfs: dict, sector_cfg: Optional[dict]) -> tuple[dict, dict]:
    """業種スコア用の (code→sector_group, {group: 合成インデックス}) を構築する。

    sector_cfg が None（業種スコア無効）または業種分類が未同期なら空 dict を返し、
    df_sector が全銘柄 None ＝ 従来挙動にフォールバックする。
    """
    if not sector_cfg:
        return {}, {}
    from data.repository import get_sector_map
    code_to_group = get_sector_map(conn)
    if not code_to_group:
        log.warning("業種分類(sectors)が空です。`python -m scripts.sync_sectors` で同期してください。"
                    "今回は業種スコアをスキップします。")
        return {}, {}
    indices = build_sector_indices(
        universe_dfs, code_to_group,
        min_constituents=int(sector_cfg.get("min_constituents", 3)),
    )
    log.info(f"業種合成インデックスを構築: {len(indices)} 業種")
    return code_to_group, indices


def _build_backtest_cfg(args, base: dict) -> dict:
    cfg = dict(base)
    if args.min_score is not None:
        cfg["min_abs_score"] = args.min_score
    return cfg


# Web/CLI から調整可能なパラメータ（フラット名）→ 反映先 cfg の対応。
# ここに無いキーは無視せず ValueError にしてタイポ・誤設定を早期に検出する。
#   min_abs_score は simulator が backtest_cfg から読むため "backtest" に置く。
_PARAM_TARGETS: dict[str, str] = {
    "atr_entry_pullback":  "trade_plan",
    "atr_stop_mult":       "trade_plan",
    "reward_risk_ratio":   "trade_plan",
    "trail_atr_mult":      "exit",
    "partial_tp_r":        "exit",
    "partial_tp_pct":      "exit",
    "move_to_breakeven":   "exit",
    "min_abs_score":       "backtest",
    "breakout_lookback":   "screening",
    "ma_short":            "screening",
    "ma_long":             "screening",
    "rsi_period":          "screening",
    "rsi_oversold":        "screening",
    "rsi_overbought":      "screening",
    "volume_spike_ratio":  "screening",
    "weekly_trend_filter": "regime",
    "adx_min":             "regime",
    "index_ma":            "regime",
}


def current_param_defaults() -> dict:
    """調整可能パラメータの現在の有効値（デフォルト⊕DB上書き）を {param: value} で返す。

    config の getter を経由するため、Webで保存した永続上書きが反映される。
    Web のバックテストフォームの初期値、およびパラメータ編集画面の土台。
    """
    src = {
        "TRADE_PLAN_CONFIG": config.get_trade_plan_config(),
        "EXIT_CONFIG":       config.get_exit_config(),
        "SCORING_CONFIG":    config.get_scoring_config(),
        "SCREENING_CONFIG":  config.get_screening_config(),
        "REGIME_CONFIG":     config.get_regime_config(),
        "RISK_CONFIG":       config.get_risk_config(),
    }
    out: dict = {}
    for key, section in config.PARAM_SECTIONS.items():
        cfg = src.get(section)
        if cfg is not None and key in cfg:
            out[key] = cfg[key]
    return out


def _apply_param_overrides(overrides: Optional[dict], cfgs: dict[str, Optional[dict]]) -> None:
    """フラットなパラメータ上書きを対応する cfg dict（コピー）に適用する。

    cfgs: {"trade_plan": ..., "exit": ..., "backtest": ..., "screening": ...,
           "scoring": ..., "regime": ...}（regime は無効時 None）
    未知のキーは ValueError。regime が無効（None）なのに regime 系キーが来た場合は無視する。
    """
    for key, value in (overrides or {}).items():
        target_name = _PARAM_TARGETS.get(key)
        if target_name is None:
            raise ValueError(f"未知のバックテストパラメータ: {key}")
        target = cfgs.get(target_name)
        if target is None:
            continue  # 例: regime 無効時に regime パラメータが渡された
        target[key] = value


def run_backtest(
    universe: str = "ALL",
    *,
    regime: bool = True,
    sector: bool = True,
    no_partial_tp: bool = False,
    min_score: Optional[float] = None,
    param_overrides: Optional[dict] = None,
    no_cache: bool = False,
    save: bool = True,
    conn=None,
) -> dict:
    """1回分のバックテストを実行して結果を返す（CLIとWeb APIで共有する純粋実行関数）。

    config の各 cfg はコピーして使う（グローバル設定を破壊しない）。param_overrides で
    個別パラメータを上書きできる。戻り値:
        {"run_id", "metrics", "trades", "no_fills", "params"}
    run_id は save=True で保存できた場合のみ非 None。
    """
    codes = config.SCREENING_UNIVERSE
    if universe == "JP":
        codes = config.SCREENING_UNIVERSE_JP
    elif universe == "US":
        codes = config.SCREENING_UNIVERSE_US

    own_conn = conn is None
    if own_conn:
        conn = get_connection()

    all_trades: list[Trade] = []
    all_no_fills: list[NoFill] = []
    index_by_market: dict[str, object] = {}
    try:
        # 永続上書き（settings）を反映した有効 config を組み立てる。
        # 上書きは「渡された conn」から読む（バックテスト DB と同一スコープで一貫させる）。
        from data.repository import get_param_overrides
        overrides = get_param_overrides(conn)
        screening_cfg  = config._effective_section("SCREENING_CONFIG", overrides)
        scoring_cfg    = config._effective_section("SCORING_CONFIG", overrides)
        trade_plan_cfg = config._effective_section("TRADE_PLAN_CONFIG", overrides)
        backtest_cfg   = dict(config.BACKTEST_CONFIG)
        # simulator は min_abs_score を backtest_cfg から読むため、永続設定値を写す。
        if "min_abs_score" in scoring_cfg:
            backtest_cfg["min_abs_score"] = scoring_cfg["min_abs_score"]
        if min_score is not None:
            backtest_cfg["min_abs_score"] = min_score
        exit_cfg = config._effective_section("EXIT_CONFIG", overrides)
        if no_partial_tp:
            exit_cfg["partial_tp_pct"] = 0.0
            exit_cfg["move_to_breakeven"] = False
        regime_cfg = config._effective_section("REGIME_CONFIG", overrides) if regime else None
        sector_cfg = None
        if sector:
            _sc = config._effective_section("SECTOR_CONFIG", overrides)
            if _sc.get("enabled", True):
                sector_cfg = _sc

        _apply_param_overrides(param_overrides, {
            "screening": screening_cfg, "scoring": scoring_cfg,
            "trade_plan": trade_plan_cfg, "backtest": backtest_cfg,
            "exit": exit_cfg, "regime": regime_cfg,
        })

        years = int(str(backtest_cfg.get("history", "5y")).rstrip("y"))
        # レジームフィルタ有効時は指数データをスキャン開始前に1回取得
        if regime_cfg:
            _fetch_index_df(regime_cfg, codes, index_by_market)

        # 日足を全銘柄プリロード（業種合成インデックスの構築に全構成銘柄が必要）
        universe_dfs: dict[str, object] = {}
        for code in codes:
            market = resolve_market(code)
            if no_cache:
                from core.data_client import StockDataClient
                client = StockDataClient(rate_limit_sec=1.0)
                df = client.get_history(code, market, period=f"{years}y", interval="1d")
            else:
                df = get_history_cached(conn, code, market, interval="1d", years=years)
            if df is None or df.empty:
                log.warning(f"  スキップ（データなし）: {code}")
                continue
            universe_dfs[code] = df

        # 業種スコア用の合成インデックスを構築（sector_cfg 有効時のみ）
        code_to_group, sector_indices = _build_sector_context(conn, universe_dfs, sector_cfg)

        for code, df in universe_dfs.items():
            market = resolve_market(code)
            log.info(f"処理中: {code} [{market.code}]")

            df_weekly = None
            if regime_cfg and regime_cfg.get("weekly_trend_filter", True):
                df_weekly = get_history_cached(conn, code, market, interval="1wk", years=years)

            df_sector = sector_series_for(code, code_to_group, sector_indices) if sector_cfg else None

            trades, no_fills = simulate_symbol(
                code, df, screening_cfg, scoring_cfg, trade_plan_cfg, backtest_cfg, exit_cfg,
                regime_cfg=regime_cfg,
                df_weekly=df_weekly,
                df_index=index_by_market.get(market.code),
                df_sector=df_sector,
                sector_cfg=sector_cfg,
            )
            all_trades.extend(trades)
            all_no_fills.extend(no_fills)

            sym = compute_metrics(trades, no_fills)
            log.info(
                f"  {code}: signal={sym['total_signals']} "
                f"fill={sym['fill_rate']*100:.0f}% "
                f"win={sym['win_rate']*100:.0f}% "
                f"avgR={sym['avg_r']:+.2f}"
            )

        total_metrics = compute_metrics(
            all_trades, all_no_fills,
            risk_cfg=config._effective_section("RISK_CONFIG", overrides),
        )

        params_snapshot = {
            "screening_cfg":  screening_cfg,
            "scoring_cfg":    scoring_cfg,
            "trade_plan_cfg": trade_plan_cfg,
            "exit_cfg":       exit_cfg,
            "backtest_cfg":   backtest_cfg,
            "regime":         bool(regime_cfg),
            "sector":         bool(sector_cfg),
        }

        run_id = None
        if save:
            from data.repository import save_backtest_run
            run_id = save_backtest_run(conn, universe, total_metrics, params_snapshot)
            log.info(f"バックテスト結果を保存しました（id={run_id}）")
    finally:
        if own_conn:
            conn.close()

    return {
        "run_id": run_id,
        "metrics": total_metrics,
        "trades": all_trades,
        "no_fills": all_no_fills,
        "params": params_snapshot,
    }


def _print_by_code(all_trades: list, all_no_fills: list) -> None:
    """銘柄別サマリーを標準出力に出す（CLI 表示専用）。"""
    by_code: dict[str, tuple[list, list]] = {}
    for t in all_trades:
        by_code.setdefault(t.code, ([], []))[0].append(t)
    for nf in all_no_fills:
        by_code.setdefault(nf.code, ([], []))[1].append(nf)
    if not by_code:
        return

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


def run(args) -> int:
    """CLI エントリ。--optimize は専用パス、通常は run_backtest() を呼んで整形出力する。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler()],
    )

    # ウォークフォワード最適化モード（cfg を構築して専用パスへ）
    if args.optimize:
        universe = config.SCREENING_UNIVERSE
        if args.universe == "JP":
            universe = config.SCREENING_UNIVERSE_JP
        elif args.universe == "US":
            universe = config.SCREENING_UNIVERSE_US
        backtest_cfg = _build_backtest_cfg(args, config.BACKTEST_CONFIG)
        exit_cfg = dict(config.EXIT_CONFIG)
        if args.no_partial_tp:
            exit_cfg["partial_tp_pct"] = 0.0
            exit_cfg["move_to_breakeven"] = False
        regime_cfg = None if args.no_regime else config.REGIME_CONFIG
        sector_cfg = None
        if not args.no_sector and config.SECTOR_CONFIG.get("enabled", True):
            sector_cfg = config.SECTOR_CONFIG
        years = int(backtest_cfg.get("history", "5y").rstrip("y"))
        return _run_optimize(args, universe, config.SCREENING_CONFIG, config.SCORING_CONFIG,
                             config.TRADE_PLAN_CONFIG, backtest_cfg, exit_cfg, regime_cfg, years,
                             sector_cfg=sector_cfg)

    result = run_backtest(
        universe=args.universe,
        regime=not args.no_regime,
        sector=not args.no_sector,
        no_partial_tp=args.no_partial_tp,
        min_score=args.min_score,
        no_cache=args.no_cache,
        save=args.save,
    )
    print("\n" + format_report(result["metrics"], title="全銘柄集計"))
    _print_by_code(result["trades"], result["no_fills"])
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
    parser.add_argument("--no-sector", action="store_true",
                        help="業種スコア成分を無効化（業種スコアありとの A/B 比較用）")
    parser.add_argument("--optimize", action="store_true",
                        help="ウォークフォワード最適化を実行（通常バックテストの代わりに）")
    parser.add_argument("--save", action="store_true",
                        help="バックテスト結果を stock.db の backtest_runs テーブルに保存")
    args = parser.parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
