"""
複数指標の合議制スコアリング

単発シグナル（クロス/ブレイク等の発生イベント）だけでは「たまたま1指標が
反応しただけ」のダマシを拾いやすい。そこで複数の指標それぞれに −1〜+1 の
点数（買い〜売り）を付け、重み付きで合算して総合スコア（−100〜+100）を出す。
スコアが高いほど多数の指標が買いで一致＝確度が高い、という考え方。

評価する観点（コンポーネント）:
  trend    … 移動平均の並び・価格との位置・長期MAの傾き（順張りの地合い）
  macd     … MACDとシグナルの位置＋ヒストグラムの拡大（モメンタム）
  rsi      … 売られすぎ/買われすぎ（逆張りの妙味。50中心で線形）
  volume   … 当日の値動き方向を出来高が裏付けているか
  breakout … ドンチャン的な高安レンジ内での位置（上限突破=強気）

各重みは config の SCORING_CONFIG で調整でき、精度改善の実験ができる。
データ欠損（計算初期のNaN）のコンポーネントは合算から除外する。
"""

from dataclasses import dataclass
from typing import Optional

import pandas as pd


# engine等がconfig未指定でも動くようにする既定値（config.SCORING_CONFIGと同等）
DEFAULT_SCORING_CONFIG = {
    "weights": {"trend": 0.30, "macd": 0.20, "rsi": 0.15, "volume": 0.15, "breakout": 0.20},
    "thresholds": {"strong": 60, "weak": 20},
    "rsi_low": 30, "rsi_high": 70, "ma_slope_lookback": 10, "min_abs_score": 0,
}


@dataclass(frozen=True)
class Component:
    name: str
    score: float    # -1.0〜+1.0
    weight: float
    detail: str


@dataclass(frozen=True)
class Consensus:
    score: float            # -100〜+100（重み付き）
    label: str              # STRONG_BUY / BUY / NEUTRAL / SELL / STRONG_SELL
    jp_label: str           # 強い買い / 買い / 中立 / 売り / 強い売り
    side: str               # BUY / SELL / NEUTRAL
    components: list[Component]


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _is_nan(*vals) -> bool:
    return any(v is None or (isinstance(v, float) and v != v) for v in vals)


# ── 各コンポーネントの採点（-1〜+1 / 欠損は None で除外） ────────────

def _score_trend(df: pd.DataFrame, cfg: dict) -> Optional[Component]:
    c = df.iloc[-1]
    if _is_nan(c["ma_short"], c["ma_long"], c["close"]):
        return None
    parts = [
        1.0 if c["ma_short"] > c["ma_long"] else -1.0,   # 短期と長期の並び
        1.0 if c["close"] > c["ma_long"] else -1.0,       # 価格と長期MAの位置
    ]
    lb = cfg.get("ma_slope_lookback", 10)
    if len(df) > lb and not _is_nan(df["ma_long"].iloc[-1], df["ma_long"].iloc[-1 - lb]):
        slope = df["ma_long"].iloc[-1] - df["ma_long"].iloc[-1 - lb]
        parts.append(1.0 if slope > 0 else -1.0)          # 長期MAの傾き
    score = sum(parts) / len(parts)
    arrow = "↑" if score > 0 else "↓" if score < 0 else "→"
    return Component("trend", score, cfg["weights"]["trend"], f"地合い{arrow}")


def _score_macd(df: pd.DataFrame, cfg: dict) -> Optional[Component]:
    c, p = df.iloc[-1], df.iloc[-2]
    if _is_nan(c["macd"], c["macd_signal"], c["macd_hist"], p["macd_hist"]):
        return None
    base = 1.0 if c["macd"] > c["macd_signal"] else -1.0
    # ヒストグラムが拡大中なら勢いを強め、縮小中なら弱める
    strength = 1.0 if abs(c["macd_hist"]) >= abs(p["macd_hist"]) else 0.5
    score = base * strength
    return Component("macd", score, cfg["weights"]["macd"],
                     f"MACD{'>' if base > 0 else '<'}シグナル")


def _score_rsi(df: pd.DataFrame, cfg: dict) -> Optional[Component]:
    r = df.iloc[-1]["rsi"]
    if _is_nan(r):
        return None
    # 50中心。売られすぎ(<low)で+1、買われすぎ(>high)で-1（逆張りの妙味）
    score = _clip((50 - r) / (50 - cfg.get("rsi_low", 30)))
    return Component("rsi", score, cfg["weights"]["rsi"], f"RSI {r:.0f}")


def _score_volume(df: pd.DataFrame, cfg: dict) -> Optional[Component]:
    c = df.iloc[-1]
    vr = c["volume_ratio"]
    if _is_nan(vr, c["close"], c["open"]):
        return None
    direction = 1.0 if c["close"] >= c["open"] else -1.0
    # 平均比 1倍=0、2倍以上で最大。出来高が値動きを裏付けるほど寄与
    magnitude = _clip(vr - 1.0, 0.0, 1.0)
    score = direction * magnitude
    return Component("volume", score, cfg["weights"]["volume"],
                     f"出来高{vr:.1f}倍")


def _score_breakout(df: pd.DataFrame, cfg: dict) -> Optional[Component]:
    c = df.iloc[-1]
    hi, lo, close = c["highest_n"], c["lowest_n"], c["close"]
    if _is_nan(hi, lo, close):
        return None
    if close > hi:
        score = 1.0
    elif close < lo:
        score = -1.0
    else:
        mid = (hi + lo) / 2
        half = (hi - lo) / 2 or 1.0
        score = _clip((close - mid) / half)   # レンジ内の位置
    return Component("breakout", score, cfg["weights"]["breakout"], "高安レンジ位置")


_COMPONENT_FUNCS = (_score_trend, _score_macd, _score_rsi, _score_volume, _score_breakout)


def _label(score: float, cfg: dict) -> tuple[str, str, str]:
    strong = cfg.get("thresholds", {}).get("strong", 60)
    weak = cfg.get("thresholds", {}).get("weak", 20)
    if score >= strong:
        return "STRONG_BUY", "強い買い", "BUY"
    if score >= weak:
        return "BUY", "買い", "BUY"
    if score <= -strong:
        return "STRONG_SELL", "強い売り", "SELL"
    if score <= -weak:
        return "SELL", "売り", "SELL"
    return "NEUTRAL", "中立", "NEUTRAL"


def compute_consensus(df: pd.DataFrame, cfg: dict) -> Optional[Consensus]:
    """指標計算済みDataFrameから合議制の総合スコアを算出する。

    有効なコンポーネントが1つも無い（データ不足）場合は None。
    """
    components = [f(df, cfg) for f in _COMPONENT_FUNCS]
    components = [c for c in components if c is not None]
    if not components:
        return None

    total_weight = sum(c.weight for c in components)
    if total_weight <= 0:
        return None
    weighted = sum(c.score * c.weight for c in components) / total_weight
    score = round(weighted * 100, 1)
    label, jp_label, side = _label(score, cfg)
    return Consensus(score=score, label=label, jp_label=jp_label,
                     side=side, components=components)
