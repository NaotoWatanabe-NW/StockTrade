"""ポートフォリオ熱量（リスク使用率）・推奨サイジングのAPI"""

from fastapi import APIRouter, Depends

import config
from api.deps import get_db, auto_expire
from api.schemas import PortfolioHeat, SizingResponse
from core.market import resolve_market
from core.risk import calc_shares, lot_size_for_market, calc_position_value
from data.repository import list_holdings, list_signals

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.get("/heat", response_model=PortfolioHeat)
def get_portfolio_heat(conn=Depends(get_db)):
    """
    現在のポートフォリオ熱量を返す。

    熱量 = 保有銘柄数 × 1トレードのリスク%（RISK_CONFIG より）
    現在保有中の銘柄数は holdings テーブルの件数を使う。
    """
    risk_cfg = config.get_risk_config()
    open_positions = len(list_holdings(conn))
    risk_pct = float(risk_cfg.get("risk_per_trade_pct", 1.0))
    max_pos  = int(risk_cfg.get("max_positions", 5))

    return PortfolioHeat(
        open_positions=open_positions,
        max_positions=max_pos,
        risk_per_trade_pct=risk_pct,
        heat_pct=open_positions * risk_pct,
        heat_max_pct=max_pos * risk_pct,
    )


@router.get("/suggestions", response_model=SizingResponse)
def get_sizing_suggestions(conn=Depends(get_db)):
    """口座サイズと固定リスク%から、OPEN な買いシグナル各々の推奨株数を返す。

    口座サイズ・リスク%・同時保有上限は RISK_CONFIG（設定でWeb編集可）から取得。
    株数は calc_shares（許容リスク額 ÷ 損切り幅、ロット単位で丸め）で算出する。
    """
    auto_expire(conn)  # 期限切れOPENを除外してから推奨を出す
    risk_cfg = config.get_risk_config()
    account  = float(risk_cfg.get("account_size", 0))
    risk_pct = float(risk_cfg.get("risk_per_trade_pct", 1.0))
    max_pos  = int(risk_cfg.get("max_positions", 5))
    open_positions = len(list_holdings(conn))

    suggestions = []
    for s in list_signals(conn, status="OPEN", limit=100):
        if s["side"] != "BUY":
            continue
        entry, stop = s.get("entry_price"), s.get("stop_price")
        if entry is None or stop is None or entry <= 0 or entry == stop:
            continue
        market_code = s.get("market") or resolve_market(s["code"]).code
        lot = lot_size_for_market(market_code)
        shares = calc_shares(account, risk_pct, entry, stop, lot)
        suggestions.append({
            "signal_id":    s["id"],
            "code":         s["code"],
            "name":         s.get("name"),
            "market":       market_code,
            "score":        s.get("score"),
            "entry_price":  entry,
            "stop_price":   stop,
            "target_price": s.get("target_price"),
            "lot_size":     lot,
            "suggested_shares": shares,
            "investment":   calc_position_value(shares, entry),
            "risk_amount":  shares * abs(entry - stop),
        })

    return SizingResponse(
        account_size=account,
        risk_per_trade_pct=risk_pct,
        max_positions=max_pos,
        open_positions=open_positions,
        remaining_slots=max(0, max_pos - open_positions),
        heat_pct=open_positions * risk_pct,
        suggestions=suggestions,
    )
