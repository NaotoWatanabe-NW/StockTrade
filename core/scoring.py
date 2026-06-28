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
    "weights": {"trend": 0.30, "macd": 0.20, "rsi": 0.15, "volume": 0.15,
                "breakout": 0.20, "sector": 0.15},
    "thresholds": {"strong": 60, "weak": 20},
    "rsi_low": 30, "rsi_high": 70, "ma_slope_lookback": 10, "min_abs_score": 0,
}

# sector コンポーネントの挙動既定値（config.SECTOR_CONFIG 相当。未指定時に使う）
DEFAULT_SECTOR_SCORING = {
    "index_ma": 50, "ma_slope_lookback": 10, "rs_lookback": 60,
    "trend_weight": 0.5, "rs_weight": 0.5, "rs_scale": 0.10,
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


def _sector_trend_score(sdf: pd.DataFrame, scfg: dict) -> Optional[float]:
    """合成業種インデックスのトレンド点（-1〜+1）。データ不足は None。

    終値が業種MAより上か＋業種MAの傾きが上向きかを平均する（trend 成分と同思想）。
    """
    ma_period = int(scfg.get("index_ma", 50))
    if len(sdf) < ma_period + 1:
        return None
    close = sdf["close"].iloc[-1]
    ma_series = sdf["close"].rolling(ma_period).mean()
    ma = ma_series.iloc[-1]
    if _is_nan(close, ma):
        return None
    parts = [1.0 if close > ma else -1.0]
    lb = int(scfg.get("ma_slope_lookback", 10))
    if len(ma_series) > lb and not _is_nan(ma_series.iloc[-1], ma_series.iloc[-1 - lb]):
        slope = ma_series.iloc[-1] - ma_series.iloc[-1 - lb]
        parts.append(1.0 if slope > 0 else -1.0)
    return sum(parts) / len(parts)


def _relative_strength_score(df: pd.DataFrame, sdf: pd.DataFrame, scfg: dict) -> Optional[float]:
    """銘柄の業種に対する相対強度点（-1〜+1）。データ不足は None。

    銘柄の N 日リターン − 業種の N 日リターンを rs_scale で割って -1〜+1 に丸める。
    業種より強い（アウトパフォーム）ほど加点。
    """
    n = int(scfg.get("rs_lookback", 60))
    if len(df) < n + 1 or len(sdf) < n + 1:
        return None
    sp_now, sp_then = df["close"].iloc[-1], df["close"].iloc[-1 - n]
    ip_now, ip_then = sdf["close"].iloc[-1], sdf["close"].iloc[-1 - n]
    if _is_nan(sp_now, sp_then, ip_now, ip_then) or sp_then == 0 or ip_then == 0:
        return None
    stock_ret = sp_now / sp_then - 1.0
    sector_ret = ip_now / ip_then - 1.0
    scale = float(scfg.get("rs_scale", 0.10)) or 0.10
    return _clip((stock_ret - sector_ret) / scale)


def _score_sector(
    df: pd.DataFrame,
    df_sector: pd.DataFrame,
    cfg: dict,
    sector_cfg: Optional[dict] = None,
) -> Optional[Component]:
    """業種トレンド＋業種内相対強度を合算した sector コンポーネント（-1〜+1）。

    df        : 銘柄の指標付き DataFrame（最新行＝現在バー）
    df_sector : 所属業種の合成インデックス（close 列）。現在バー日付で `<= t` にスライス
                して読む（未来参照なし。レジームフィルタと同じ流儀）。
    cfg       : SCORING_CONFIG（重みは weights["sector"]）
    sector_cfg: SECTOR_CONFIG（index_ma / rs_lookback 等の挙動）

    業種データが薄い・期間不足で両サブ点とも算出不能なら None（合算から除外）。
    """
    if df_sector is None or "close" not in df_sector.columns:
        return None
    scfg = {**DEFAULT_SECTOR_SCORING, **(sector_cfg or {})}
    weight = cfg.get("weights", {}).get("sector", 0.0)

    # 現在バーまでの業種インデックスに揃える（ルックアヘッド回避）
    t_ts = pd.Timestamp(df.index[-1])
    sidx = df_sector.index
    if getattr(sidx, "tz", None) is not None:
        sidx = sidx.tz_localize(None)
    sdf = df_sector.loc[sidx <= t_ts]
    if sdf.empty:
        return None

    trend = _sector_trend_score(sdf, scfg)
    rs = _relative_strength_score(df, sdf, scfg)
    if trend is None and rs is None:
        return None

    tw = float(scfg.get("trend_weight", 0.5))
    rw = float(scfg.get("rs_weight", 0.5))
    num, den = 0.0, 0.0
    if trend is not None:
        num += tw * trend; den += tw
    if rs is not None:
        num += rw * rs; den += rw
    score = num / den if den > 0 else 0.0

    t_arrow = "→" if trend is None else ("↑" if trend > 0 else "↓" if trend < 0 else "→")
    rs_txt = "-" if rs is None else f"{rs:+.1f}"
    return Component("sector", score, weight, f"業種{t_arrow} RS{rs_txt}")


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


def compute_consensus(
    df: pd.DataFrame,
    cfg: dict,
    df_sector: Optional[pd.DataFrame] = None,
    sector_cfg: Optional[dict] = None,
) -> Optional[Consensus]:
    """指標計算済みDataFrameから合議制の総合スコアを算出する。

    df_sector を渡すと業種トレンド＋相対強度の sector コンポーネントを加える。
    None のときは従来どおり（成分を出さず、総重みの正規化で従来比率に戻る）。

    有効なコンポーネントが1つも無い（データ不足）場合は None。
    """
    components = [f(df, cfg) for f in _COMPONENT_FUNCS]
    components = [c for c in components if c is not None]
    if df_sector is not None:
        sc = _score_sector(df, df_sector, cfg, sector_cfg)
        if sc is not None:
            components.append(sc)
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
