"""ポートフォリオ熱量（リスク使用率）のAPI"""

from fastapi import APIRouter, Depends

import config
from api.deps import get_db
from api.schemas import PortfolioHeat
from data.repository import list_holdings

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.get("/heat", response_model=PortfolioHeat)
def get_portfolio_heat(conn=Depends(get_db)):
    """
    現在のポートフォリオ熱量を返す。

    熱量 = 保有銘柄数 × 1トレードのリスク%（RISK_CONFIG より）
    現在保有中の銘柄数は holdings テーブルの件数を使う。
    """
    risk_cfg = config.RISK_CONFIG
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
