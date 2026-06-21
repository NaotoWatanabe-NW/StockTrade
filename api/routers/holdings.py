"""保有・監視銘柄のCRUD API"""

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_db
from api.schemas import HoldingIn, HoldingOut
from data.repository import list_holdings, get_holding, upsert_holding, delete_holding

router = APIRouter(prefix="/api/holdings", tags=["holdings"])


@router.get("", response_model=list[HoldingOut])
def get_holdings(conn=Depends(get_db)):
    return list_holdings(conn)


@router.post("", response_model=HoldingOut)
def create_or_update_holding(holding: HoldingIn, conn=Depends(get_db)):
    """コードをキーに登録/更新（同じコードを送ると上書き）"""
    return upsert_holding(conn, **holding.model_dump())


@router.get("/{code}", response_model=HoldingOut)
def read_holding(code: str, conn=Depends(get_db)):
    h = get_holding(conn, code)
    if h is None:
        raise HTTPException(status_code=404, detail=f"未登録の銘柄: {code}")
    return h


@router.delete("/{code}")
def remove_holding(code: str, conn=Depends(get_db)):
    if not delete_holding(conn, code):
        raise HTTPException(status_code=404, detail=f"未登録の銘柄: {code}")
    return {"deleted": code}
