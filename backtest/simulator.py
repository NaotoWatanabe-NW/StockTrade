"""
バックテスト・シミュレータ

1 銘柄分の OHLCV を受け取り、過去バーを順番に「現時点」として
シグナル→エントリー試行→出口管理の一連を再現する。

───────────────────────────────────────────────────────────
ルックアヘッド回避（設計上の不変条件）
  - 指標は df_indicators[:t+1] のスライスのみで計算済み列を使う。
  - エントリー注文の充足判定は **signal バーの翌足以降** の高安を使う。
  - 約定価格は指値=指値価格、逆指値=逆指値価格（スリッページは別途設定）。
───────────────────────────────────────────────────────────

エントリー有効期限 = 15 営業日（BACKTEST_CONFIG["entry_order_valid_days"]）
  → 有効期限内に充足しなければ no_fill として記録。

出口ロジック（core/exit_rules.py が担う）
  1. タイムストップ  : bars_held >= time_stop_days
  2. 損切り         : 安値 <= current_stop
  3. 第1利確（部分） : 高値 >= entry + partial_tp_r × risk → partial_tp_pct 分を利確
                      → current_stop を建値へ引き上げ（move_to_breakeven=True の場合）
  4. ATRトレーリング : 部分利確後、高値 − trail_atr_mult × ATR > current_stop で更新
  5. 最終利確       : 高値 >= target（2R 地点）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from core.exit_rules import ExitState, update_exit
from core.indicators import add_technical_indicators, detect_signals
from core.regime import apply_regime_filters
from core.scoring import compute_consensus
from core.trade_plan import net_side, entry_style, build_trade_plan


@dataclass
class Trade:
    """1 トレードの記録（部分利確フィールドを含む）"""
    code: str
    signal_bar: int
    signal_date: str
    signal_types: list[str]
    side: str                   # "BUY"
    entry_kind: str             # "LIMIT" / "STOP"
    entry_price: float
    stop_price: float
    target_price: Optional[float]
    risk: float                 # entry - stop（正値）

    filled: bool = False
    fill_date: Optional[str] = None
    fill_bar: Optional[int] = None
    fill_price: Optional[float] = None

    closed: bool = False
    exit_date: Optional[str] = None
    exit_bar: Optional[int] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None  # "STOP_LOSS" / "TAKE_PROFIT" / "TIME_STOP"

    # 部分利確（Phase 2）
    partial_tp_price: Optional[float] = None   # 第1利確の価格
    partial_tp_pct: float = 0.0                # 第1利確で手仕舞った割合

    score: Optional[float] = None
    bars_held: Optional[int] = None


@dataclass
class NoFill:
    """エントリー注文が有効期限内に約定しなかった記録"""
    code: str
    signal_bar: int
    signal_date: str
    signal_types: list[str]
    side: str
    entry_kind: str
    entry_price: float
    score: Optional[float] = None


def _date_str(idx) -> str:
    """pandas Index の要素を YYYY-MM-DD 文字列に変換する。"""
    try:
        return str(idx.date())
    except AttributeError:
        return str(idx)[:10]


def _nan_to_none(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def simulate_symbol(
    code: str,
    df_raw: pd.DataFrame,
    cfg: dict,
    scoring_cfg: dict,
    trade_plan_cfg: dict,
    backtest_cfg: dict,
    exit_cfg: Optional[dict] = None,
    regime_cfg: Optional[dict] = None,
    df_weekly: Optional[pd.DataFrame] = None,
    df_index: Optional[pd.DataFrame] = None,
) -> tuple[list[Trade], list[NoFill]]:
    """
    1 銘柄に対してバックテストを実行し、トレード履歴と不約定記録を返す。

    df_raw     : 生 OHLCV（指標列は内部で計算する）
    exit_cfg   : EXIT_CONFIG（None の場合は backtest_cfg のデフォルト値で動く簡易版）
    regime_cfg : REGIME_CONFIG（None の場合はレジームフィルタを適用しない）
    df_weekly  : 銘柄の週足 OHLCV（週足トレンドフィルタに使用）
    df_index   : 指数の日足 OHLCV（指数レジームフィルタに使用）

    backtest_cfg の主なキー:
        entry_order_valid_days : エントリー注文の有効期限（営業日数）
        slippage_atr           : 約定時のスリッページ（ATR の倍率）
        min_abs_score          : この絶対スコア未満のシグナルはスキップ
    """
    entry_valid   = int(backtest_cfg.get("entry_order_valid_days", 15))
    slippage_mult = float(backtest_cfg.get("slippage_atr", 0.0))
    min_abs_score = float(backtest_cfg.get("min_abs_score", 0))

    # exit_cfg が渡されない場合、backtest_cfg から互換的に組み立てる
    if exit_cfg is None:
        exit_cfg = {
            "time_stop_days":    int(backtest_cfg.get("max_hold_bars", 20)),
            "partial_tp_r":      1.0,
            "partial_tp_pct":    0.0,   # 0 = 部分利確なし（Phase 1 互換）
            "move_to_breakeven": False,
            "trail_atr_mult":    2.0,
        }

    min_len = cfg.get("ma_long", 25) + 2
    if df_raw is None or len(df_raw) < min_len:
        return [], []

    # 指標を全足分まとめて計算（ループ内での再計算を避ける）
    df = add_technical_indicators(df_raw.copy(), cfg)

    trades: list[Trade] = []
    no_fills: list[NoFill] = []
    pending: list[Trade] = []

    # 建玉ごとの出口状態（trade オブジェクトと 1:1 対応）
    exit_states: dict[int, ExitState] = {}  # id(trade) -> ExitState

    for t in range(min_len, len(df)):
        today_bar = df.iloc[t]
        today_date = _date_str(df.index[t])
        lo    = float(today_bar["low"])
        hi    = float(today_bar["high"])
        close = float(today_bar["close"])
        atr_now = _nan_to_none(today_bar.get("atr"))

        # ── 1. 建玉中トレードの出口チェック（exit_rules に委譲） ────────
        for tr in [x for x in trades if x.filled and not x.closed]:
            state = exit_states[id(tr)]
            sig = update_exit(state, lo, hi, close, atr_now, exit_cfg)

            # 部分利確フィールドを記録（最終決済前に発生した場合も保持）
            if sig.partial_tp_price is not None and tr.partial_tp_price is None:
                tr.partial_tp_price = sig.partial_tp_price
                tr.partial_tp_pct   = sig.partial_tp_pct

            if sig.reason is not None:
                tr.closed     = True
                tr.exit_price = sig.exit_price
                tr.exit_reason = sig.reason
                tr.exit_date  = today_date
                tr.exit_bar   = t
                tr.bars_held  = state.bars_held

        # ── 2. エントリー待ち注文の充足チェック ──────────────────────
        still_pending: list[Trade] = []
        for tr in pending:
            bars_waiting = t - tr.signal_bar
            slip = slippage_mult * (atr_now or 0.0)

            if tr.entry_kind == "LIMIT" and lo <= tr.entry_price:
                tr.fill_price = tr.entry_price + slip
                tr.filled     = True
                tr.fill_date  = today_date
                tr.fill_bar   = t
                trades.append(tr)
                exit_states[id(tr)] = ExitState(
                    entry_price=tr.fill_price,
                    initial_stop=tr.stop_price,
                    current_stop=tr.stop_price,
                    target=tr.target_price,
                    atr=atr_now or tr.risk / 2,
                )
            elif tr.entry_kind == "STOP" and hi >= tr.entry_price:
                tr.fill_price = tr.entry_price + slip
                tr.filled     = True
                tr.fill_date  = today_date
                tr.fill_bar   = t
                trades.append(tr)
                exit_states[id(tr)] = ExitState(
                    entry_price=tr.fill_price,
                    initial_stop=tr.stop_price,
                    current_stop=tr.stop_price,
                    target=tr.target_price,
                    atr=atr_now or tr.risk / 2,
                )
            elif bars_waiting >= entry_valid:
                no_fills.append(NoFill(
                    code=code,
                    signal_bar=tr.signal_bar,
                    signal_date=tr.signal_date,
                    signal_types=tr.signal_types,
                    side=tr.side,
                    entry_kind=tr.entry_kind,
                    entry_price=tr.entry_price,
                    score=tr.score,
                ))
            else:
                still_pending.append(tr)

        pending = still_pending

        # ── 3. シグナル判定（df[:t+1] のスライスで、t は今日） ───────
        df_t = df.iloc[:t + 1]
        signals = detect_signals(df_t, cfg)
        if not signals:
            continue

        side = net_side(signals)
        if side != "BUY":
            continue

        # ── 3a. レジームフィルタ（regime_cfg が渡されたときのみ適用）────
        if regime_cfg:
            t_date = df.index[t]
            rf = apply_regime_filters(df_t, t_date, df_weekly, df_index, regime_cfg)
            if not rf["passed"]:
                continue

        consensus = compute_consensus(df_t, scoring_cfg)
        score = consensus.score if consensus else None

        if min_abs_score > 0 and (score is None or abs(score) < min_abs_score):
            continue

        atr_val = _nan_to_none(today_bar.get("atr"))
        style   = entry_style(signals)
        plan    = build_trade_plan(side, float(today_bar["close"]), atr_val, trade_plan_cfg, style)
        if plan is None:
            continue

        risk = plan["entry"] - plan["stop"]
        if risk <= 0:
            continue

        pending.append(Trade(
            code=code,
            signal_bar=t,
            signal_date=today_date,
            signal_types=[s["type"] for s in signals],
            side=side,
            entry_kind=plan["entry_kind"],
            entry_price=plan["entry"],
            stop_price=plan["stop"],
            target_price=plan.get("target"),
            risk=risk,
            score=score,
        ))

    # ループ終了後も残った pending は no_fill 扱い
    for tr in pending:
        no_fills.append(NoFill(
            code=code, signal_bar=tr.signal_bar, signal_date=tr.signal_date,
            signal_types=tr.signal_types, side=tr.side,
            entry_kind=tr.entry_kind, entry_price=tr.entry_price, score=tr.score,
        ))

    return trades, no_fills
