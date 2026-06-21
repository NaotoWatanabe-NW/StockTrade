"""
テクニカル指標計算

スイングトレード向けに以下を計算:
  - 移動平均（短期・長期）とゴールデン/デッドクロス
  - RSI（買われすぎ・売られすぎ）
  - 出来高急増検知
  - 高値・安値ブレイクアウト
  - ATR（ボラティリティ＝指値・損切り計算の基準）

各シグナルには売買方向 side（BUY/SELL/NEUTRAL）を付与し、
core.trade_plan が注文プランを組み立てられるようにしている。
"""

import pandas as pd
import numpy as np


def add_technical_indicators(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """OHLCV DataFrameにテクニカル指標列を追加"""
    d = df.copy()

    # 移動平均
    d["ma_short"] = d["close"].rolling(config["ma_short"]).mean()
    d["ma_long"]  = d["close"].rolling(config["ma_long"]).mean()

    # RSI
    delta = d["close"].diff()
    gain  = delta.clip(lower=0).rolling(config["rsi_period"]).mean()
    loss  = (-delta.clip(upper=0)).rolling(config["rsi_period"]).mean()
    d["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, 1e-10))

    # 出来高移動平均・急増率
    d["volume_avg"]   = d["volume"].rolling(config["volume_avg_period"]).mean()
    d["volume_ratio"] = d["volume"] / d["volume_avg"].replace(0, np.nan)

    # 高値・安値ブレイクアウト判定用
    lookback = config["breakout_lookback"]
    d["highest_n"] = d["high"].rolling(lookback).max().shift(1)  # 当日除く過去N本の最高値
    d["lowest_n"]  = d["low"].rolling(lookback).min().shift(1)

    # ATR（指値・損切りの基準となる値幅）
    tr = pd.concat([
        d["high"] - d["low"],
        (d["high"] - d["close"].shift()).abs(),
        (d["low"]  - d["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    d["atr"] = tr.rolling(14).mean()

    # MACD（12/26/9）。合議制スコアのモメンタム要素に使用
    ema_fast = d["close"].ewm(span=12, adjust=False).mean()
    ema_slow = d["close"].ewm(span=26, adjust=False).mean()
    d["macd"]        = ema_fast - ema_slow
    d["macd_signal"] = d["macd"].ewm(span=9, adjust=False).mean()
    d["macd_hist"]   = d["macd"] - d["macd_signal"]

    return d


def detect_signals(df: pd.DataFrame, config: dict) -> list[dict]:
    """
    最新足に対してシグナル判定を行う

    戻り値: シグナル辞書のリスト（複数同時発生もありうる）
        {"type", "side", "label", "detail"}
        side は "BUY" / "SELL" / "NEUTRAL"
    """
    if len(df) < config["ma_long"] + 2:
        return []

    signals = []
    prev = df.iloc[-2]
    curr = df.iloc[-1]

    # ── ゴールデンクロス / デッドクロス ──────────────
    if pd.notna(prev["ma_short"]) and pd.notna(prev["ma_long"]):
        if prev["ma_short"] <= prev["ma_long"] and curr["ma_short"] > curr["ma_long"]:
            signals.append({
                "type": "GOLDEN_CROSS", "side": "BUY", "label": "🟢 ゴールデンクロス",
                "detail": f"MA{config['ma_short']}がMA{config['ma_long']}を上抜け",
            })
        if prev["ma_short"] >= prev["ma_long"] and curr["ma_short"] < curr["ma_long"]:
            signals.append({
                "type": "DEAD_CROSS", "side": "SELL", "label": "🔴 デッドクロス",
                "detail": f"MA{config['ma_short']}がMA{config['ma_long']}を下抜け",
            })

    # ── RSI 売られすぎ・買われすぎからの回復 ──────────
    if pd.notna(prev["rsi"]) and pd.notna(curr["rsi"]):
        if prev["rsi"] < config["rsi_oversold"] and curr["rsi"] >= config["rsi_oversold"]:
            signals.append({
                "type": "RSI_REBOUND", "side": "BUY", "label": "🟢 RSI売られすぎから回復",
                "detail": f"RSI {prev['rsi']:.1f} → {curr['rsi']:.1f}",
            })
        if prev["rsi"] > config["rsi_overbought"] and curr["rsi"] <= config["rsi_overbought"]:
            signals.append({
                "type": "RSI_PULLBACK", "side": "SELL", "label": "🔴 RSI買われすぎから反落",
                "detail": f"RSI {prev['rsi']:.1f} → {curr['rsi']:.1f}",
            })

    # ── 出来高急増（方向は当日の陰陽で判断する中立シグナル）─────
    if pd.notna(curr["volume_ratio"]) and curr["volume_ratio"] >= config["volume_spike_ratio"]:
        up = curr["close"] >= curr["open"]
        signals.append({
            "type": "VOLUME_SPIKE", "side": "NEUTRAL",
            "label": f"📊 出来高急増（{'上昇' if up else '下落'}）",
            "detail": f"平均の{curr['volume_ratio']:.1f}倍",
        })

    # ── 高値・安値ブレイクアウト ───────────────────────
    if pd.notna(curr["highest_n"]) and curr["close"] > curr["highest_n"]:
        signals.append({
            "type": "BREAKOUT_HIGH", "side": "BUY", "label": "🚀 高値ブレイクアウト",
            "detail": f"過去{config['breakout_lookback']}日高値を更新",
        })
    if pd.notna(curr["lowest_n"]) and curr["close"] < curr["lowest_n"]:
        signals.append({
            "type": "BREAKOUT_LOW", "side": "SELL", "label": "📉 安値ブレイクダウン",
            "detail": f"過去{config['breakout_lookback']}日安値を割り込み",
        })

    return signals
