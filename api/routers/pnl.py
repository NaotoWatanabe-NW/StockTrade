"""損益集計のAPI（約定履歴ベースの実現損益）"""

from fastapi import APIRouter, Depends

from api.deps import get_db
from api.schemas import PnlRow
from data.repository import realized_pnl

router = APIRouter(prefix="/api/pnl", tags=["pnl"])


@router.get("", response_model=list[PnlRow])
def get_realized_pnl(conn=Depends(get_db)):
    """銘柄ごとの実現損益（平均取得単価ベース）"""
    return realized_pnl(conn)
