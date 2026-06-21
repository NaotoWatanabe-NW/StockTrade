"""
バックテスト評価メトリクス

simulate_symbol が返す Trade / NoFill のリストから
戦略の品質を数値で表す。

メトリクス一覧（STRATEGY_PLAN.md §7 に準拠）:
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
"""

from __future__ import annotations

from typing import Sequence

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


def compute_metrics(
    trades: Sequence[Trade],
    no_fills: Sequence[NoFill],
) -> dict:
    """
    Trade / NoFill のリストからパフォーマンス指標を計算して dict で返す。
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

    return {
        "total_signals":  total_signals,
        "filled":         filled,
        "fill_rate":      fill_rate,
        "closed":         closed,
        "wins":           win_count,
        "losses":         len(losses),
        "win_rate":       win_rate,
        "avg_r":          avg_r,
        "profit_factor":  profit_factor,
        "max_drawdown_r": max_dd,
        "avg_bars_held":  avg_bars,
        "time_stop_rate": time_stop_rate,
        "exit_breakdown": exit_breakdown,
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
        f"  平均保有バー   : {m['avg_bars_held']:.1f}",
        f"  タイムストップ率: {m['time_stop_rate']*100:.1f}%",
        f"  出口内訳       : {m['exit_breakdown']}",
        f"{'='*50}",
    ]
    return "\n".join(lines)
