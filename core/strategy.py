"""
単一の意思決定関数

バックテストとライブ通知が「まったく同じロジック」を通ることを保証するための
中心モジュール。

evaluate() は df の最新バーを「現時点」として扱う純粋関数。
  - ライブ通知: df = 最新の全履歴
  - バックテスト: df = df_full[:t+1]（バー t 時点の観測として呼ぶ）

戻り値 Decision には trade_plan までを含め、注文タイプへの変換（SBI用語）と
コード・銘柄名などのメタデータは呼び出し元（engine/simulator）が付加する。

Phase 3 以降でフィルタ（レジーム・決算回避 等）を追加するときは
Decision.filters と内部のフィルタ処理を拡張する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from core.indicators import add_technical_indicators, detect_signals
from core.risk import calc_shares, lot_size_for_market
from core.scoring import compute_consensus, Consensus, DEFAULT_SCORING_CONFIG
from core.trade_plan import net_side, entry_style, build_trade_plan


@dataclass
class Decision:
    """1銘柄・1時点の意思決定結果"""
    price: float
    change_pct: float
    avg_volume: float
    atr: Optional[float]
    signals: list[dict]
    consensus: Optional[Consensus]
    trade_plan: Optional[dict]
    shares: Optional[int] = None            # サイジングで算出した推奨株数（Phase 2+）
    filters: dict = field(default_factory=dict)  # Phase 3+: regime/events の通過状況


def evaluate(
    df: pd.DataFrame,
    cfg: dict,
    scoring_cfg: Optional[dict] = None,
    trade_plan_cfg: Optional[dict] = None,
    risk_cfg: Optional[dict] = None,
    market_code: str = "JP",
) -> Optional[Decision]:
    """
    OHLCV DataFrame の最新バーに対して意思決定を行い Decision を返す。

    df に指標列が無ければ内部で add_technical_indicators を呼ぶ。
    バックテストで全足分の指標を事前計算してスライスを渡す場合は、
    既存列が検出されるので再計算は行わない（ルックアヘッド無し）。

    データ不足（ma_long 本未満）の場合は None を返す。
    """
    scoring_cfg = scoring_cfg or DEFAULT_SCORING_CONFIG
    trade_plan_cfg = trade_plan_cfg or {}

    min_len = cfg.get("ma_long", 25) + 2
    if df is None or len(df) < min_len:
        return None

    # 指標列が未計算なら追加（外から渡せば再計算しない）
    if "ma_short" not in df.columns:
        df = add_technical_indicators(df, cfg)

    signals = detect_signals(df, cfg)
    consensus = compute_consensus(df, scoring_cfg)

    latest = df.iloc[-1]
    prev = df.iloc[-2]
    price = float(latest["close"])
    prev_close = float(prev["close"])
    change_pct = (price - prev_close) / prev_close * 100
    avg_volume = float(df["volume"].tail(20).mean())
    atr_raw = latest.get("atr")
    atr = float(atr_raw) if atr_raw is not None and atr_raw == atr_raw else None

    side = net_side(signals)
    style = entry_style(signals)
    plan = build_trade_plan(side, price, atr, trade_plan_cfg, style) if trade_plan_cfg else None

    # サイジング（risk_cfg が渡されていて trade_plan がある場合のみ計算）
    shares: Optional[int] = None
    if risk_cfg and plan and plan.get("entry") and plan.get("stop"):
        lot = lot_size_for_market(market_code)
        shares = calc_shares(
            account_size=risk_cfg.get("account_size", 1_000_000),
            risk_per_trade_pct=risk_cfg.get("risk_per_trade_pct", 1.0),
            entry_price=plan["entry"],
            stop_price=plan["stop"],
            lot_size=lot,
        )

    return Decision(
        price=price,
        change_pct=change_pct,
        avg_volume=avg_volume,
        atr=atr,
        signals=signals,
        consensus=consensus,
        trade_plan=plan,
        shares=shares,
    )
