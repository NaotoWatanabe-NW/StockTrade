"""
出口状態機械（Phase 2）

保有中ポジションの各バーにおける出口判断を担う。
バックテスト（simulator.py）とライブ通知の両方から同じロジックを呼ぶ。

── 出口ロジック（優先順位順）─────────────────────────────────────
1. タイムストップ  : bars_held >= time_stop_days で引け終値手仕舞い
2. 損切り         : 安値が current_stop を下回ったら損切り
3. 第1利確（部分） : 高値が 1R 地点を超えたら partial_tp_pct 分を利確
                    → current_stop を建値（entry_price）へ引き上げ
4. ATRトレーリング : 部分利確後、高値 − trail_atr_mult × ATR が
                    current_stop を上回れば current_stop を引き上げ
5. 利確（残り）    : 高値が target（= initial 2R 地点）を超えたら残り全部利確

── EXIT_CONFIG パラメータ ────────────────────────────────────────
time_stop_days    : 保有中タイムストップ（営業日）
partial_tp_r      : 第1利確を何R地点で取るか
partial_tp_pct    : 第1利確で手仕舞う割合（例: 0.5 = 50%）
move_to_breakeven : 第1利確後に current_stop を建値に引き上げるか
trail_atr_mult    : ATRトレーリングの幅（倍率）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ExitState:
    """1 ポジション分の出口管理状態。バーごとに update_exit() で更新する。"""
    entry_price: float
    initial_stop: float
    current_stop: float
    target: Optional[float]     # 最終利確ターゲット（None なら ATR トレーリングのみ）
    atr: float                  # エントリー時の ATR（トレーリング基準）
    bars_held: int = 0

    partial_taken: bool = False
    partial_tp_price: Optional[float] = None
    partial_tp_pct: float = 0.0


@dataclass
class ExitSignal:
    """update_exit() の戻り値。手仕舞いが不要なら reason=None。"""
    reason: Optional[str]       # "STOP_LOSS" / "TAKE_PROFIT" / "TIME_STOP" / None
    exit_price: Optional[float] = None
    partial_tp_price: Optional[float] = None  # 同バーで部分利確が発生した場合
    partial_tp_pct: float = 0.0
    updated_stop: Optional[float] = None      # current_stop の更新値


def update_exit(
    state: ExitState,
    bar_low: float,
    bar_high: float,
    bar_close: float,
    bar_atr: Optional[float],
    cfg: dict,
) -> ExitSignal:
    """
    1 バー分の出口判断を行い ExitSignal を返す。
    state は参照渡しで直接更新される（current_stop、partial_taken 等）。

    cfg : EXIT_CONFIG（config.py の EXIT_CONFIG と同形式）
    """
    time_stop_days = int(cfg.get("time_stop_days", 15))
    partial_tp_r   = float(cfg.get("partial_tp_r", 1.0))
    partial_tp_pct = float(cfg.get("partial_tp_pct", 0.5))
    move_be        = bool(cfg.get("move_to_breakeven", True))
    trail_mult     = float(cfg.get("trail_atr_mult", 2.0))

    state.bars_held += 1
    risk = state.entry_price - state.initial_stop  # 正値

    # ── 1. タイムストップ（最優先） ─────────────────────────────
    if state.bars_held >= time_stop_days:
        return ExitSignal(reason="TIME_STOP", exit_price=bar_close)

    # ── 2. 損切り ────────────────────────────────────────────────
    if bar_low <= state.current_stop:
        return ExitSignal(reason="STOP_LOSS", exit_price=state.current_stop)

    # ── 3. 第1利確（まだ取っていない場合） ───────────────────────
    partial_signal: Optional[float] = None
    if not state.partial_taken and risk > 0:
        partial_target = state.entry_price + partial_tp_r * risk
        if bar_high >= partial_target:
            state.partial_taken = True
            state.partial_tp_price = partial_target
            partial_signal = partial_target
            if move_be:
                state.current_stop = max(state.current_stop, state.entry_price)

    # ── 4. ATRトレーリング（部分利確後） ─────────────────────────
    updated_stop: Optional[float] = None
    if state.partial_taken:
        atr_now = bar_atr if bar_atr and bar_atr > 0 else state.atr
        trail_stop = bar_high - trail_mult * atr_now
        if trail_stop > state.current_stop:
            state.current_stop = trail_stop
            updated_stop = trail_stop

    # ── 5. 最終利確（target に達した場合） ──────────────────────
    if state.target is not None and bar_high >= state.target:
        return ExitSignal(
            reason="TAKE_PROFIT",
            exit_price=state.target,
            partial_tp_price=partial_signal,
            partial_tp_pct=partial_tp_pct if partial_signal else 0.0,
            updated_stop=updated_stop,
        )

    # 手仕舞いなし（部分利確のみ発生した可能性あり）
    return ExitSignal(
        reason=None,
        exit_price=None,
        partial_tp_price=partial_signal,
        partial_tp_pct=partial_tp_pct if partial_signal else 0.0,
        updated_stop=updated_stop,
    )
