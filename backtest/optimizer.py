"""
ウォークフォワード最適化（Phase 4）

IS（インサンプル）期間でパラメータをグリッドサーチし、
OOS（アウトオブサンプル）期間で汎化性能を検証する。

──────────────────────────────────────────────────────────
窓の構造（OPTIMIZE_CONFIG のデフォルト: IS=3y, OOS=1y, step=1y）
  Window 0:  IS=[year1〜year3]  OOS=[year4]
  Window 1:  IS=[year2〜year4]  OOS=[year5]

各 IS 期間で最良パラメータを選択 → OOS で適用 → OOS 結果を集計。
IS と OOS の両方でオーバーフィットしていないか確認する。

最適化対象パラメータ（OPTIMIZE_CONFIG["param_grid"]）:
  min_abs_score     : シグナルスコアフィルタ（backtest_cfg）
  trail_atr_mult    : ATRトレーリング幅（exit_cfg）
  partial_tp_r      : 第1利確 R 地点（exit_cfg）
  breakout_lookback : ブレイクアウト判定期間（screening_cfg）

目的関数（objective）: profit_factor / avg_r / win_rate のいずれか
──────────────────────────────────────────────────────────
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

import pandas as pd

from backtest.metrics import compute_metrics
from backtest.simulator import Trade, NoFill, simulate_symbol
from core.indicators import add_technical_indicators

log = logging.getLogger(__name__)

# パラメータ名 → (config の種類, dict キー) のマッピング
_PARAM_MAP: dict[str, tuple[str, str]] = {
    "min_abs_score":     ("backtest_cfg", "min_abs_score"),
    "trail_atr_mult":    ("exit_cfg",     "trail_atr_mult"),
    "partial_tp_r":      ("exit_cfg",     "partial_tp_r"),
    "breakout_lookback": ("screening_cfg","breakout_lookback"),
}


@dataclass
class WindowResult:
    """1ウィンドウ（IS + OOS）の結果"""
    window_idx: int
    is_start: str
    is_end: str
    oos_start: str
    oos_end: str
    best_params: dict
    is_metrics: dict
    oos_metrics: dict
    oos_trades: list[Trade] = field(default_factory=list)
    oos_no_fills: list[NoFill] = field(default_factory=list)


@dataclass
class WalkForwardResult:
    """全ウィンドウを束ねた最終結果"""
    windows: list[WindowResult]
    combined_oos_metrics: dict   # 全 OOS トレードの集計
    recommended_params: dict     # 直近 IS ウィンドウで選ばれた最良パラメータ


def _apply_params(
    screening_cfg: dict,
    backtest_cfg: dict,
    exit_cfg: dict,
    params: dict,
) -> tuple[dict, dict, dict]:
    """params を各 config dict に上書きした浅いコピーを返す。"""
    scr = dict(screening_cfg)
    bt  = dict(backtest_cfg)
    ex  = dict(exit_cfg)
    cfg_map = {"screening_cfg": scr, "backtest_cfg": bt, "exit_cfg": ex}
    for k, v in params.items():
        cfg_key, dict_key = _PARAM_MAP[k]
        cfg_map[cfg_key][dict_key] = v
    return scr, bt, ex


def _make_windows(
    earliest: pd.Timestamp,
    latest: pd.Timestamp,
    is_years: int,
    oos_years: int,
    step_years: int,
) -> list[dict]:
    """IS/OOS 日付ウィンドウのリストを生成する。"""
    windows = []
    is_delta  = timedelta(days=is_years  * 365)
    oos_delta = timedelta(days=oos_years * 365)
    step_delta = timedelta(days=step_years * 365)

    is_start = earliest
    while True:
        is_end   = is_start + is_delta
        oos_end  = is_end   + oos_delta
        if oos_end > latest:
            break
        windows.append({
            "is_start":  is_start,
            "is_end":    is_end,
            "oos_start": is_end,
            "oos_end":   oos_end,
        })
        is_start += step_delta

    return windows


def _slice_df(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """DataFrame を日付範囲でスライスする（指標列は落とす）。"""
    idx = df.index
    if hasattr(idx, "tz") and idx.tz is not None:
        start = start.tz_localize(idx.tz) if start.tzinfo is None else start.tz_convert(idx.tz)
        end   = end.tz_localize(idx.tz)   if end.tzinfo   is None else end.tz_convert(idx.tz)
    mask = (idx >= start) & (idx < end)
    sliced = df.loc[mask]
    # 指標列を除いた生 OHLCV のみを返す（simulator が内部で再計算する）
    base_cols = [c for c in ["open", "high", "low", "close", "volume"] if c in sliced.columns]
    return sliced[base_cols]


def _objective(metrics: dict, objective: str) -> float:
    """IS メトリクスから目的関数値を計算する。"""
    if metrics.get("closed", 0) == 0:
        return 0.0
    if objective == "profit_factor":
        pf = metrics.get("profit_factor", 0)
        return min(pf, 10.0)   # inf を上限で丸める
    if objective == "avg_r":
        return float(metrics.get("avg_r", 0))
    if objective == "win_rate":
        return float(metrics.get("win_rate", 0))
    # composite: avg_r × win_rate（期待値と勝率を同時に重視）
    return float(metrics.get("avg_r", 0)) * float(metrics.get("win_rate", 0))


def _grid_combinations(param_grid: dict) -> list[dict]:
    """param_grid から全組み合わせリストを生成する。"""
    keys = list(param_grid.keys())
    vals = list(param_grid.values())
    return [dict(zip(keys, combo)) for combo in itertools.product(*vals)]


def run_walk_forward(
    universe_dfs: dict[str, pd.DataFrame],   # {code: raw_ohlcv_df}
    screening_cfg: dict,
    scoring_cfg: dict,
    trade_plan_cfg: dict,
    backtest_cfg: dict,
    exit_cfg: dict,
    optimize_cfg: dict,
    regime_cfg: Optional[dict] = None,
    weekly_dfs: Optional[dict[str, pd.DataFrame]] = None,
    index_dfs: Optional[dict[str, pd.DataFrame]] = None,
) -> WalkForwardResult:
    """
    ウォークフォワード最適化を実行する。

    universe_dfs : {code: raw_ohlcv_df}  各銘柄の全期間 OHLCV
    weekly_dfs   : {code: weekly_ohlcv_df}（レジームフィルタ用。省略可）
    index_dfs    : {market_code: index_ohlcv_df}（レジームフィルタ用。省略可）
    """
    is_years    = int(optimize_cfg.get("is_years",   3))
    oos_years   = int(optimize_cfg.get("oos_years",  1))
    step_years  = int(optimize_cfg.get("step_years", 1))
    objective   = optimize_cfg.get("objective", "profit_factor")
    param_grid  = optimize_cfg.get("param_grid", {})

    combos = _grid_combinations(param_grid)
    log.info(f"グリッド組み合わせ数: {len(combos)}")

    # 全銘柄から共通の日付範囲を求める
    valid_dfs = [df for df in universe_dfs.values() if df is not None and not df.empty]
    if not valid_dfs:
        raise ValueError("有効な銘柄データが1件もありません。")
    earliest = min(df.index.min() for df in valid_dfs)
    latest   = max(df.index.max() for df in valid_dfs)

    # timezone を除去して naive timestamp に統一
    if hasattr(earliest, "tz") and earliest.tz is not None:
        earliest = earliest.tz_localize(None)
    if hasattr(latest, "tz") and latest.tz is not None:
        latest = latest.tz_localize(None)

    windows = _make_windows(earliest, latest, is_years, oos_years, step_years)
    if not windows:
        raise ValueError(
            f"ウィンドウが生成できません。データ期間 {earliest.date()}〜{latest.date()} に対して "
            f"IS={is_years}y + OOS={oos_years}y が収まらない。"
        )

    log.info(f"ウォークフォワード: {len(windows)} ウィンドウ")

    window_results: list[WindowResult] = []

    for wi, win in enumerate(windows):
        log.info(
            f"\n── ウィンドウ {wi} ──  "
            f"IS: {win['is_start'].date()}〜{win['is_end'].date()}  "
            f"OOS: {win['oos_start'].date()}〜{win['oos_end'].date()}"
        )

        # IS スライス（全銘柄分）
        is_dfs  = {c: _slice_df(df, win["is_start"], win["is_end"])
                   for c, df in universe_dfs.items()}
        oos_dfs = {c: _slice_df(df, win["oos_start"], win["oos_end"])
                   for c, df in universe_dfs.items()}

        # ──────────────────────────────────
        # グリッドサーチ（IS 期間）
        # ──────────────────────────────────
        best_score  = -float("inf")
        best_params = combos[0]
        best_is_metrics: dict = {}

        for ci, params in enumerate(combos):
            scr, bt, ex = _apply_params(screening_cfg, backtest_cfg, exit_cfg, params)
            is_trades: list[Trade] = []
            is_nofills: list[NoFill] = []

            for code, df_is in is_dfs.items():
                if df_is is None or df_is.empty:
                    continue
                from core.market import resolve_market
                mkt = resolve_market(code)
                df_wk = (weekly_dfs or {}).get(code)
                df_ix = (index_dfs or {}).get(mkt.code)
                t, nf = simulate_symbol(
                    code, df_is, scr, scoring_cfg, trade_plan_cfg, bt, ex,
                    regime_cfg=regime_cfg,
                    df_weekly=df_wk,
                    df_index=df_ix,
                )
                is_trades.extend(t)
                is_nofills.extend(nf)

            m = compute_metrics(is_trades, is_nofills)
            score = _objective(m, objective)
            if score > best_score:
                best_score = score
                best_params = params
                best_is_metrics = m

            if (ci + 1) % 10 == 0:
                log.info(f"  グリッド進捗: {ci+1}/{len(combos)}")

        log.info(f"  最良パラメータ (IS): {best_params}  score={best_score:.3f}")

        # ──────────────────────────────────
        # OOS 評価（最良パラメータを適用）
        # ──────────────────────────────────
        scr, bt, ex = _apply_params(screening_cfg, backtest_cfg, exit_cfg, best_params)
        oos_trades: list[Trade] = []
        oos_nofills: list[NoFill] = []

        for code, df_oos in oos_dfs.items():
            if df_oos is None or df_oos.empty:
                continue
            from core.market import resolve_market
            mkt = resolve_market(code)
            df_wk = (weekly_dfs or {}).get(code)
            df_ix = (index_dfs or {}).get(mkt.code)
            t, nf = simulate_symbol(
                code, df_oos, scr, scoring_cfg, trade_plan_cfg, bt, ex,
                regime_cfg=regime_cfg,
                df_weekly=df_wk,
                df_index=df_ix,
            )
            oos_trades.extend(t)
            oos_nofills.extend(nf)

        oos_metrics = compute_metrics(oos_trades, oos_nofills)
        log.info(
            f"  OOS: signal={oos_metrics['total_signals']} "
            f"win={oos_metrics['win_rate']*100:.0f}% "
            f"avgR={oos_metrics['avg_r']:+.3f} "
            f"PF={oos_metrics['profit_factor']:.2f}"
        )

        window_results.append(WindowResult(
            window_idx=wi,
            is_start=str(win["is_start"].date()),
            is_end=str(win["is_end"].date()),
            oos_start=str(win["oos_start"].date()),
            oos_end=str(win["oos_end"].date()),
            best_params=best_params,
            is_metrics=best_is_metrics,
            oos_metrics=oos_metrics,
            oos_trades=oos_trades,
            oos_no_fills=oos_nofills,
        ))

    # ──────────────────────────────────────────
    # 全 OOS トレードを結合して最終集計
    # ──────────────────────────────────────────
    all_oos_trades: list[Trade] = []
    all_oos_nofills: list[NoFill] = []
    for wr in window_results:
        all_oos_trades.extend(wr.oos_trades)
        all_oos_nofills.extend(wr.oos_no_fills)

    combined_metrics = compute_metrics(all_oos_trades, all_oos_nofills)

    # 推奨パラメータ = 直近（最後）ウィンドウの IS で選ばれたパラメータ
    recommended = window_results[-1].best_params if window_results else {}

    return WalkForwardResult(
        windows=window_results,
        combined_oos_metrics=combined_metrics,
        recommended_params=recommended,
    )


def format_wf_report(result: WalkForwardResult) -> str:
    """ウォークフォワード結果のテキストレポートを生成する。"""
    from backtest.metrics import format_report

    lines = ["=" * 60, "  ウォークフォワード最適化レポート", "=" * 60]

    for wr in result.windows:
        lines.append(f"\n── ウィンドウ {wr.window_idx} ──")
        lines.append(f"  IS:  {wr.is_start} 〜 {wr.is_end}")
        lines.append(f"  OOS: {wr.oos_start} 〜 {wr.oos_end}")
        lines.append(f"  最良パラメータ: {wr.best_params}")
        m_is  = wr.is_metrics
        m_oos = wr.oos_metrics

        def _row(label, is_val, oos_val):
            return f"  {label:<18} IS: {is_val}   OOS: {oos_val}"

        def _pf(m):
            pf = m.get("profit_factor", 0)
            return "∞" if pf == float("inf") else f"{pf:.2f}"

        lines.append(_row("シグナル数",
                          m_is.get("total_signals", 0),
                          m_oos.get("total_signals", 0)))
        lines.append(_row("勝率",
                          f"{m_is.get('win_rate',0)*100:.1f}%",
                          f"{m_oos.get('win_rate',0)*100:.1f}%"))
        lines.append(_row("平均R",
                          f"{m_is.get('avg_r',0):+.3f}",
                          f"{m_oos.get('avg_r',0):+.3f}"))
        lines.append(_row("PF", _pf(m_is), _pf(m_oos)))

    lines.append("\n" + "=" * 60)
    lines.append("  全 OOS 集計")
    lines.append("=" * 60)
    lines.append(format_report(result.combined_oos_metrics, title="OOS Combined"))

    lines.append("\n推奨パラメータ（直近 IS 最良値）:")
    for k, v in result.recommended_params.items():
        lines.append(f"  {k}: {v}")

    # config.py へのコピー用スニペット
    lines.append("\n" + "─" * 60)
    lines.append("config.py への反映スニペット（該当箇所に貼り付けてください）:")
    lines.append("─" * 60)
    p = result.recommended_params
    snippet_lines = []
    # EXIT_CONFIG に反映するキー
    exit_keys = {"trail_atr_mult", "partial_tp_r"}
    exit_updates = {k: v for k, v in p.items() if k in exit_keys}
    if exit_updates:
        snippet_lines.append("EXIT_CONFIG = {")
        snippet_lines.append("    # ── ウォークフォワード推奨値 ──")
        for k, v in exit_updates.items():
            snippet_lines.append(f'    "{k}": {v},')
        snippet_lines.append("    # （他のキーはそのまま維持）")
        snippet_lines.append("}")
    # BACKTEST_CONFIG / SCORING_CONFIG に反映するキー
    backtest_keys = {"min_abs_score"}
    backtest_updates = {k: v for k, v in p.items() if k in backtest_keys}
    if backtest_updates:
        snippet_lines.append("BACKTEST_CONFIG / SCORING_CONFIG:")
        for k, v in backtest_updates.items():
            snippet_lines.append(f'    "{k}": {v},')
    # SCREENING_CONFIG に反映するキー
    screening_keys = {"breakout_lookback"}
    screening_updates = {k: v for k, v in p.items() if k in screening_keys}
    if screening_updates:
        snippet_lines.append("SCREENING_CONFIG:")
        for k, v in screening_updates.items():
            snippet_lines.append(f'    "{k}": {v},')
    for sl in snippet_lines:
        lines.append(sl)

    return "\n".join(lines)
