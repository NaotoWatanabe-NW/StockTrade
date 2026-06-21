"""
バックテスト評価メトリクス

simulate_symbol が返す Trade / NoFill のリストから
戦略の品質を数値で表す。

メトリクス一覧:
    total_signals   : シグナル発生数（filled + no_fill）
    filled          : 約定件数
    fill_rate       : 約定率 = filled / total_signals
    closed          : 決済済み件数（filled のうち出口が確定したもの）
    wins            : 勝ちトレード数
    losses          : 負けトレード数
    win_rate        : 勝率 = wins / closed
    avg_r           : 平均損益（R単位）= closed の平均 pnl_r
    profit_factor   : 総利益R / 総損失R
    max_drawdown_r  : 累積 R 曲線の最大ドローダウン（R単位）
    avg_bars_held   : 平均保有バー数
    time_stop_rate  : タイムストップ終了の割合
    exit_breakdown  : 各出口理由の件数 dict
    sharpe_ratio    : シャープレシオ（年率換算）
    annual_return_pct: 年率リターン%（複利、risk_cfg 指定時のみ算出）
    equity_curve    : 資産曲線 [{"date": str, "equity": float}]（1.0 始点）
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime
from typing import Optional, Sequence

from backtest.simulator import Trade, NoFill


def _pnl_r(trade: Trade) -> float:
    """
    1 トレードの損益（R 単位）を返す。未決済は 0 として扱う。

    部分利確がある場合:
        total_R = partial_tp_pct × (partial_tp_price − fill_price) / risk
                + (1 − partial_tp_pct) × (exit_price − fill_price) / risk
    """
    if not trade.closed or trade.exit_price is None or trade.fill_price is None:
        return 0.0
    if trade.risk <= 0:
        return 0.0
    entry = trade.fill_price
    remaining = 1.0 - trade.partial_tp_pct
    partial_r = 0.0
    if trade.partial_tp_price is not None and trade.partial_tp_pct > 0:
        partial_r = trade.partial_tp_pct * (trade.partial_tp_price - entry) / trade.risk
    final_r = remaining * (trade.exit_price - entry) / trade.risk
    return partial_r + final_r


def _max_drawdown(r_series: list[float]) -> float:
    """累積 R 曲線の最大ドローダウンを返す（負値）。"""
    if not r_series:
        return 0.0
    peak = 0.0
    max_dd = 0.0
    cumulative = 0.0
    for r in r_series:
        cumulative += r
        if cumulative > peak:
            peak = cumulative
        dd = cumulative - peak
        if dd < max_dd:
            max_dd = dd
    return max_dd


def _parse_date(d: Optional[str]) -> Optional[datetime]:
    """YYYY-MM-DD 文字列を datetime に変換（失敗時は None）。"""
    if not d:
        return None
    try:
        return datetime.strptime(d[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _sharpe_ratio(rs: list[float], date_range_years: float) -> float:
    """
    年率換算シャープレシオを返す。

    各トレードの R を期間全体で年率換算した収益指標とする。
    trades_per_year = len(rs) / years で取引頻度を推定して年率化。
    """
    if len(rs) < 2:
        return 0.0
    avg = sum(rs) / len(rs)
    try:
        std = statistics.stdev(rs)
    except statistics.StatisticsError:
        return 0.0
    if std == 0.0:
        return 0.0
    trades_per_year = max(len(rs) / max(date_range_years, 0.1), 1.0)
    return avg / std * math.sqrt(trades_per_year)


def _equity_curve(
    closed_trades: list[Trade],
    rs: list[float],
    risk_pct_frac: float,
    max_positions: int = 1,
) -> list[dict]:
    """
    資産曲線を生成する。

    決済日でソートし、各トレード後の複利資産額（初期=1.0）を返す。
    risk_pct_frac : 1R = account の何割か（例: 0.01 = 1%）
    max_positions : 最大同時保有ポジション数。複数ポジションが同時並行する場合、
                    1トレードの資産への実際の寄与は risk_pct/max_positions となる。
                    この調整で「全トレードを逐次実行」による過大評価を補正する。

    ⚠️ 注意: 実際の資産曲線はポジション重複タイミングや口座規模に依存するため、
             この計算は近似値（単純化した上限目安）である。
    """
    dated = [
        (t.exit_date or "", r)
        for t, r in zip(closed_trades, rs)
        if t.exit_date
    ]
    dated.sort(key=lambda x: x[0])

    # 同時保有ポジション数で割ることで逐次複利の過大評価を補正
    adj_risk = risk_pct_frac / max(max_positions, 1)

    equity = 1.0
    curve = []
    for date_str, r in dated:
        equity *= (1.0 + r * adj_risk)
        curve.append({"date": date_str[:10], "equity": round(equity, 6)})
    return curve


def _annual_return_pct(equity_curve: list[dict], date_range_years: float) -> float:
    """最終資産倍率から年率リターン%を計算する（近似値）。"""
    if not equity_curve:
        return 0.0
    final = equity_curve[-1]["equity"]
    if final <= 0 or date_range_years <= 0:
        return 0.0
    return (final ** (1.0 / date_range_years) - 1.0) * 100.0


def compute_metrics(
    trades: Sequence[Trade],
    no_fills: Sequence[NoFill],
    risk_cfg: Optional[dict] = None,
) -> dict:
    """
    Trade / NoFill のリストからパフォーマンス指標を計算して dict で返す。

    risk_cfg が指定された場合は年率リターン%・シャープレシオ・資産曲線を追加する。
    """
    filled_trades = [t for t in trades if t.filled]
    closed_trades = [t for t in filled_trades if t.closed]

    total_signals = len(filled_trades) + len(no_fills)
    filled = len(filled_trades)
    fill_rate = filled / total_signals if total_signals > 0 else 0.0

    rs = [_pnl_r(t) for t in closed_trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]

    closed = len(closed_trades)
    win_count = len(wins)
    win_rate = win_count / closed if closed > 0 else 0.0
    avg_r = sum(rs) / closed if closed > 0 else 0.0

    total_gain = sum(wins)
    total_loss = abs(sum(losses))
    profit_factor = total_gain / total_loss if total_loss > 0 else float("inf")

    max_dd = _max_drawdown(rs)

    bars_held = [t.bars_held for t in closed_trades if t.bars_held is not None]
    avg_bars = sum(bars_held) / len(bars_held) if bars_held else 0.0

    exit_breakdown: dict[str, int] = {}
    for t in closed_trades:
        reason = t.exit_reason or "UNKNOWN"
        exit_breakdown[reason] = exit_breakdown.get(reason, 0) + 1

    time_stop_count = exit_breakdown.get("TIME_STOP", 0)
    time_stop_rate = time_stop_count / closed if closed > 0 else 0.0

    # ── Phase 7: 高度メトリクス ──────────────────────────────
    # 日付範囲を推定（closed trades の signal_date / exit_date から）
    date_range_years = 0.0
    all_dt = []
    for t in closed_trades:
        for d in (t.signal_date, t.exit_date):
            dt = _parse_date(d)
            if dt:
                all_dt.append(dt)
    if len(all_dt) >= 2:
        date_range_years = max((max(all_dt) - min(all_dt)).days / 365.0, 0.1)

    risk_pct_frac = float((risk_cfg or {}).get("risk_per_trade_pct", 1.0)) / 100.0
    max_positions = int((risk_cfg or {}).get("max_positions", 1))
    curve = _equity_curve(closed_trades, rs, risk_pct_frac, max_positions=max_positions)
    sharpe = _sharpe_ratio(rs, date_range_years)
    ann_ret = _annual_return_pct(curve, date_range_years)

    return {
        "total_signals":     total_signals,
        "filled":            filled,
        "fill_rate":         fill_rate,
        "closed":            closed,
        "wins":              win_count,
        "losses":            len(losses),
        "win_rate":          win_rate,
        "avg_r":             avg_r,
        "profit_factor":     profit_factor,
        "max_drawdown_r":    max_dd,
        "avg_bars_held":     avg_bars,
        "time_stop_rate":    time_stop_rate,
        "exit_breakdown":    exit_breakdown,
        # Phase 7 additions
        "sharpe_ratio":      sharpe,
        "annual_return_pct": ann_ret,
        "equity_curve":      curve,
    }


def format_report(metrics: dict, title: str = "バックテスト結果") -> str:
    """メトリクスを人間が読めるレポート文字列にフォーマットする。"""
    m = metrics
    pf = m["profit_factor"]
    pf_str = f"{pf:.2f}" if pf != float("inf") else "∞"

    lines = [
        f"{'='*50}",
        f"  {title}",
        f"{'='*50}",
        f"  シグナル数     : {m['total_signals']}",
        f"  約定数         : {m['filled']}",
        f"  約定率         : {m['fill_rate']*100:.1f}%",
        f"  決済数         : {m['closed']}",
        f"  勝ち           : {m['wins']}",
        f"  負け           : {m['losses']}",
        f"  勝率           : {m['win_rate']*100:.1f}%",
        f"  平均損益(R)    : {m['avg_r']:+.3f}",
        f"  プロフィットF  : {pf_str}",
        f"  最大DD(R)      : {m['max_drawdown_r']:.3f}",
        f"  シャープレシオ : {m.get('sharpe_ratio', 0.0):.2f}",
        f"  年率リターン   : {m.get('annual_return_pct', 0.0):+.1f}%（近似・同時ポジション補正済）",
        f"  平均保有バー   : {m['avg_bars_held']:.1f}",
        f"  タイムストップ率: {m['time_stop_rate']*100:.1f}%",
        f"  出口内訳       : {m['exit_breakdown']}",
        f"{'='*50}",
    ]
    return "\n".join(lines)
