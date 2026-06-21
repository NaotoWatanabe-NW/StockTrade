"""約定履歴（取引記録）のAPI"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_db
from api.schemas import TradeIn, TradeOut
from data.repository import list_trades, add_trade, delete_trade, sync_holding_from_trades, get_trade

router = APIRouter(prefix="/api/trades", tags=["trades"])


@router.get("", response_model=list[TradeOut])
def get_trades(code: Optional[str] = None, conn=Depends(get_db)):
    return list_trades(conn, code)


@router.post("", response_model=TradeOut)
def create_trade(trade: TradeIn, conn=Depends(get_db)):
    try:
        result = add_trade(conn, **trade.model_dump())
        sync_holding_from_trades(conn, result["code"])
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{trade_id}")
def remove_trade(trade_id: int, conn=Depends(get_db)):
    trade = get_trade(conn, trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail=f"未登録の取引: {trade_id}")
    delete_trade(conn, trade_id)
    sync_holding_from_trades(conn, trade["code"])
    return {"deleted": trade_id}
