"""ウォッチリスト（スクリーニング対象銘柄）のCRUD API"""

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_db
from api.schemas import WatchlistIn, WatchlistOut
from data.repository import list_watchlist, upsert_watchlist, delete_watchlist_item

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


@router.get("", response_model=list[WatchlistOut])
def get_watchlist(conn=Depends(get_db)):
    return list_watchlist(conn)


@router.post("", response_model=WatchlistOut)
def create_or_update_watchlist_item(item: WatchlistIn, conn=Depends(get_db)):
    return upsert_watchlist(conn, **item.model_dump())


@router.delete("/{code}")
def remove_watchlist_item(code: str, conn=Depends(get_db)):
    if not delete_watchlist_item(conn, code):
        raise HTTPException(status_code=404, detail=f"未登録の銘柄: {code}")
    return {"deleted": code}
