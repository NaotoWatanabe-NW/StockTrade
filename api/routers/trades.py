"""約定履歴（取引記録）のAPI"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_db
from api.schemas import TradeIn, TradeOut
from data.repository import list_trades, add_trade, delete_trade

router = APIRouter(prefix="/api/trades", tags=["trades"])


@router.get("", response_model=list[TradeOut])
def get_trades(code: Optional[str] = None, conn=Depends(get_db)):
    return list_trades(conn, code)


@router.post("", response_model=TradeOut)
def create_trade(trade: TradeIn, conn=Depends(get_db)):
    try:
        return add_trade(conn, **trade.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{trade_id}")
def remove_trade(trade_id: int, conn=Depends(get_db)):
    if not delete_trade(conn, trade_id):
        raise HTTPException(status_code=404, detail=f"未登録の取引: {trade_id}")
    return {"deleted": trade_id}
