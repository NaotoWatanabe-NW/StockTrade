"""
売買タイミングの具体価格（指値・損切り・利確）の計算

SBI証券にはAPIがないため発注は手動。だが「シグナルが出た」だけでは
実際にいくらで注文を置けばよいか分からない。ここでは ATR（平均的な
日中値幅）を基準に、エントリー指値／逆指値・損切り・利確の目安価格を
算出する。算出した価格は core.orders が SBI の注文タイプ
（指値・逆指値・OCO・IFD・IFDOCO）に組み立てる。

エントリーの建て方（entry_style）:
  PULLBACK … 押し目買い／戻り売り。順張りの一服を待つ → 指値(LIMIT)
  BREAKOUT … 高値/安値ブレイクに乗る。飛び乗り       → 逆指値(STOP)

考え方:
  BUY（新規・買い増し候補）
    指値/逆指値 = 現値 ∓ pullback × ATR   … 押し目は下、ブレイクは上に置く
    損切り      = エントリー − stop_mult × ATR
    利確        = エントリー + RR × (エントリー−損切り)
  SELL（保有ロングの手仕舞い）
    戻り売り指値 = 現値 + pullback × ATR   … 戻りを拾って利確
    撤退逆指値   = 現値 − stop_mult × ATR  … 下抜けたら損切り
"""

from typing import Optional

# シグナル種別 → 売買方向
SIGNAL_SIDE = {
    "GOLDEN_CROSS":  "BUY",
    "RSI_REBOUND":   "BUY",
    "BREAKOUT_HIGH": "BUY",
    "DEAD_CROSS":    "SELL",
    "RSI_PULLBACK":  "SELL",
    "BREAKOUT_LOW":  "SELL",
    # VOLUME_SPIKE は方向が文脈依存なので中立
}

# ブレイクアウト系シグナル（飛び乗り＝逆指値が適する）
_BREAKOUT_TYPES = {"BREAKOUT_HIGH", "BREAKOUT_LOW"}


def net_side(signals: list[dict]) -> str:
    """複数シグナルから総合的な売買方向を決める（BUY / SELL / NEUTRAL）"""
    buy = sum(1 for s in signals if s.get("side") == "BUY")
    sell = sum(1 for s in signals if s.get("side") == "SELL")
    if buy > sell:
        return "BUY"
    if sell > buy:
        return "SELL"
    return "NEUTRAL"


def entry_style(signals: list[dict]) -> str:
    """エントリーの建て方を判定（BREAKOUT=逆指値 / PULLBACK=指値）"""
    if any(s.get("type") in _BREAKOUT_TYPES for s in signals):
        return "BREAKOUT"
    return "PULLBACK"


def build_trade_plan(
    side: str,
    price: float,
    atr: Optional[float],
    cfg: dict,
    style: str = "PULLBACK",
) -> Optional[dict]:
    """
    売買方向・現値・ATRから価格プランを算出する。

    cfg:
        atr_entry_pullback : エントリーを現値から何ATR離すか
        atr_stop_mult      : 損切りを何ATR離すか
        reward_risk_ratio  : 利確 = リスク幅 × この倍率（BUYのみ）

    戻り値（BUY例）:
        {"side","entry_kind","entry","stop","target","risk_pct","reward_pct"}
        entry_kind: "LIMIT"(指値) / "STOP"(逆指値)
    ATRが無効、または side が NEUTRAL の場合は None。
    """
    if side not in ("BUY", "SELL"):
        return None
    if price is None or atr is None or atr <= 0 or price <= 0:
        return None

    pullback = cfg["atr_entry_pullback"] * atr
    stop_dist = cfg["atr_stop_mult"] * atr

    if side == "BUY":
        if style == "BREAKOUT":
            entry = price + pullback        # 上放れに乗る → 逆指値（買い）
            entry_kind = "STOP"
        else:
            entry = price - pullback        # 押し目を待つ → 指値（買い）
            entry_kind = "LIMIT"
        stop = entry - stop_dist
        risk = entry - stop
        target = entry + cfg["reward_risk_ratio"] * risk
        return {
            "side": "BUY",
            "entry_kind": entry_kind,
            "entry": entry,
            "stop": stop,
            "target": target,
            "risk_pct": risk / entry * 100,
            "reward_pct": (target - entry) / entry * 100,
        }

    # SELL: 保有ロングの手仕舞い（戻り売り指値 ＋ 撤退逆指値）
    take = price + pullback      # 戻り売り（利確）→ 指値
    stop = price - stop_dist     # 撤退（損切り）→ 逆指値
    return {
        "side": "SELL",
        "entry_kind": "LIMIT",
        "entry": take,
        "stop": stop,
        "target": None,
        "risk_pct": (price - stop) / price * 100,
        "reward_pct": None,
    }
