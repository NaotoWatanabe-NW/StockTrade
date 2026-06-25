"""
シグナルの「予測」に対する実勢価格の結果評価

signals テーブルに記録した計画（entry/stop/target）が、生成後の実勢価格で
どう決着したかを判定する。実際に取引したかどうかには依存しない――
シグナル（=予測）そのものの的中度を測り、score → 勝率/期待R の
キャリブレーションに使うための土台。

評価ロジック（BUY のみ。SELL は手仕舞い指示で入口の予測ではないため対象外）:
  1. 約定判定 … 生成翌足以降 entry_valid_days 営業日以内に計画 entry に到達したか
       LIMIT: 安値 <= entry（押し目に届いた） / STOP: 高値 >= entry（上抜けた）
       到達しなければ NO_ENTRY。
  2. 決着判定 … 約定の翌足以降 horizon_days 営業日を見て、
       stop 到達（安値 <= stop）と target 到達（高値 >= target）の先着で決める。
       同一足で両方に触れた場合は保守的に STOP 優先（悲観評価）。
       期間内に未決着なら TIMEOUT（期間末の終値で realized_r を算出）。
  3. 評価期間が未経過なら PENDING（その時点までの mfe/mae を暫定記録）。

ルックアヘッド回避: 呼び出し元は「生成日より後」のバーだけを df で渡すこと。
バーの先着判定は翌足以降のみを用いる（約定足と同一足での決着は数えない）。
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

# outcome の取り得る値
NO_ENTRY = "NO_ENTRY"
TARGET = "TARGET"
STOP = "STOP"
TIMEOUT = "TIMEOUT"
PENDING = "PENDING"


def _date_str(idx_value) -> str:
    """DataFrame インデックス要素を YYYY-MM-DD 文字列に変換する。"""
    try:
        return str(idx_value.date())
    except AttributeError:
        return str(idx_value)[:10]


def evaluate_signal_outcome(
    signal: dict,
    df: pd.DataFrame,
    horizon_days: int,
    entry_valid_days: int,
) -> Optional[dict]:
    """
    1 シグナルの予測結果を評価して signal_outcomes 列に対応する dict を返す。

    signal : signals 行の dict（side, entry_price, stop_price, target_price,
             risk, entry_kind を使用）
    df     : 生成日より後の日足 OHLC（昇順・DatetimeIndex、列 open/high/low/close）

    戻り値 : 結果 dict。評価不能（BUY 以外 / risk 不正 / entry 未設定）なら None。
             まだ評価期間が足りない場合は outcome=PENDING の暫定結果を返す。
    """
    if signal.get("side") != "BUY":
        return None  # SELL（手仕舞い指示）は入口予測ではないため対象外

    entry = signal.get("entry_price")
    stop = signal.get("stop_price")
    risk = signal.get("risk")
    if risk is None and entry is not None and stop is not None:
        risk = abs(entry - stop)
    if entry is None or risk is None or risk <= 0:
        return None  # R を定義できないシグナルは評価対象外

    target = signal.get("target_price")
    entry_kind = signal.get("entry_kind") or "LIMIT"

    base = {
        "horizon_days": horizon_days,
        "entry_filled": False,
        "entry_fill_date": None,
        "outcome": None,
        "hit_target": False,
        "hit_stop": False,
        "days_to_resolve": None,
        "mfe_r": None,
        "mae_r": None,
        "close_at_horizon": None,
        "realized_r": None,
        "eval_through": None,
    }

    if df is None or len(df) == 0:
        base["outcome"] = PENDING
        return base

    base["eval_through"] = _date_str(df.index[-1])

    # ── 1. 約定（計画 entry への到達）判定 ───────────────────────
    fill_pos = None
    for i in range(len(df)):
        if i >= entry_valid_days:
            break
        bar = df.iloc[i]
        hi, lo = float(bar["high"]), float(bar["low"])
        touched = lo <= entry if entry_kind == "LIMIT" else hi >= entry
        if touched:
            fill_pos = i
            base["entry_filled"] = True
            base["entry_fill_date"] = _date_str(df.index[i])
            break

    if fill_pos is None:
        # 有効期限を見終えていれば NO_ENTRY 確定。まだ足りなければ PENDING。
        if len(df) >= entry_valid_days:
            base["outcome"] = NO_ENTRY
        else:
            base["outcome"] = PENDING
        return base

    # ── 2. 決着（stop/target の先着）判定。約定の翌足以降を見る ──
    post = df.iloc[fill_pos + 1:]
    mfe_r = 0.0
    mae_r = 0.0
    for j in range(len(post)):
        if j >= horizon_days:
            break
        bar = post.iloc[j]
        hi, lo, close = float(bar["high"]), float(bar["low"]), float(bar["close"])
        mfe_r = max(mfe_r, (hi - entry) / risk)
        mae_r = min(mae_r, (lo - entry) / risk)
        base["mfe_r"] = mfe_r
        base["mae_r"] = mae_r
        base["eval_through"] = _date_str(post.index[j])

        hit_stop = lo <= stop if stop is not None else False
        hit_target = hi >= target if target is not None else False

        if hit_stop:  # 同一足で両方なら保守的に STOP 優先
            base["outcome"] = STOP
            base["hit_stop"] = True
            base["days_to_resolve"] = j + 1
            base["realized_r"] = (stop - entry) / risk
            return base
        if hit_target:
            base["outcome"] = TARGET
            base["hit_target"] = True
            base["days_to_resolve"] = j + 1
            base["realized_r"] = (target - entry) / risk
            return base

    # ── 3. 期間内に未決着 ────────────────────────────────────
    available = len(post)
    if available >= horizon_days:
        # 期間を満了 → TIMEOUT（期間末の終値で評価）
        last = post.iloc[horizon_days - 1]
        base["outcome"] = TIMEOUT
        base["close_at_horizon"] = float(last["close"])
        base["realized_r"] = (float(last["close"]) - entry) / risk
        base["days_to_resolve"] = horizon_days
    else:
        # まだ期間が足りない → 暫定（PENDING）
        base["outcome"] = PENDING

    return base
