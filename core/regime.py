"""
レジーム・上位足フィルタ（Phase 3）

シグナルが出ても「地合いが悪い」ときはエントリーを見送る。
3 種類のフィルタを提供し、strategy.evaluate() / backtest/simulator.py から呼ぶ。

────────────────────────────────────────────────────────
フィルタ一覧
  weekly_trend  : 週足終値 > 週足 MA(20) → 上昇トレンド相場
  index_regime  : 指数終値 > 指数 MA(50) → 地合いが強気
  adx_strength  : 日足 ADX >= adx_min  → トレンド相場（レンジ回避）
────────────────────────────────────────────────────────

各関数は DataFrame スライス（バーtまでの履歴）を受け取る純粋関数。
データ取得（yfinance）はせず、呼び出し元が渡す。
→ テスト容易・ルックアヘッド回避・ライブ/バックテスト共通。

戻り値: True = 通過（エントリー可） / False = 見送り
"""

from __future__ import annotations

from typing import Optional

import pandas as pd


def check_weekly_trend(
    df_weekly: Optional[pd.DataFrame],
    t_date,
    cfg: dict,
) -> bool:
    """
    週足トレンドフィルタ。

    df_weekly : 週足 OHLCV（DatetimeIndex）。None なら pass。
    t_date    : 判定基準日（datetime.date または Timestamp）
    cfg       : REGIME_CONFIG

    週足 MA(20) より終値が上 → True（上昇トレンド）。
    データ不足や None の場合は True（素通り）。
    """
    if not cfg.get("weekly_trend_filter", True):
        return True
    if df_weekly is None or len(df_weekly) < 20:
        return True  # データ不足は素通り

    # t_date 以前の最新週足バーを取得
    t_ts = pd.Timestamp(t_date)
    wdf = df_weekly[df_weekly.index <= t_ts]
    if len(wdf) < 20:
        return True

    close = wdf["close"].iloc[-1]
    ma20  = wdf["close"].rolling(20).mean().iloc[-1]
    if pd.isna(ma20):
        return True
    return bool(close > ma20)


def check_index_regime(
    df_index: Optional[pd.DataFrame],
    t_date,
    cfg: dict,
) -> bool:
    """
    代表指数レジームフィルタ。

    df_index : 指数の日足 OHLCV（DatetimeIndex）。None なら pass。
    t_date   : 判定基準日
    cfg      : REGIME_CONFIG（index_ma キーで MA 期間を指定）

    指数終値 > 指数 MA(index_ma) → True（強気地合い）。
    データ不足や None の場合は True（素通り）。
    """
    ma_period = int(cfg.get("index_ma", 50))
    if df_index is None or len(df_index) < ma_period:
        return True

    t_ts = pd.Timestamp(t_date)
    idf = df_index[df_index.index <= t_ts]
    if len(idf) < ma_period:
        return True

    close = idf["close"].iloc[-1]
    ma    = idf["close"].rolling(ma_period).mean().iloc[-1]
    if pd.isna(ma):
        return True
    return bool(close > ma)


def check_adx_strength(
    df_daily_with_indicators: pd.DataFrame,
    cfg: dict,
) -> bool:
    """
    ADX トレンド強度フィルタ。

    df_daily_with_indicators : 指標計算済みの日足スライス（add_technical_indicators 適用済み）
    cfg : REGIME_CONFIG（adx_min キーで閾値を指定）

    ADX >= adx_min → True（トレンド相場）。
    ADX 列が無いか NaN なら True（素通り）。
    """
    adx_min = float(cfg.get("adx_min", 20))
    if "adx" not in df_daily_with_indicators.columns:
        return True
    adx_val = df_daily_with_indicators["adx"].iloc[-1]
    if pd.isna(adx_val):
        return True
    return bool(adx_val >= adx_min)


def apply_regime_filters(
    df_daily_with_indicators: pd.DataFrame,
    t_date,
    df_weekly: Optional[pd.DataFrame],
    df_index: Optional[pd.DataFrame],
    cfg: dict,
) -> dict:
    """
    3 つのフィルタをまとめて適用し、結果を dict で返す。

    戻り値:
        {
            "weekly_trend": True/False,
            "index_regime": True/False,
            "adx":          True/False,
            "passed":       True/False,  # 全フィルタ通過
        }
    """
    weekly = check_weekly_trend(df_weekly, t_date, cfg)
    index  = check_index_regime(df_index, t_date, cfg)
    adx    = check_adx_strength(df_daily_with_indicators, cfg)
    return {
        "weekly_trend": weekly,
        "index_regime": index,
        "adx":          adx,
        "passed":       weekly and index and adx,
    }
