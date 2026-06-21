"""シグナル追跡のAPI（実取引フィードバックループ）"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_db
from api.schemas import SignalOut, SignalStatusIn, SignalAttribution
from data.repository import (
    list_signals, get_signal, update_signal_status, signal_attribution,
)

router = APIRouter(prefix="/api/signals", tags=["signals"])


@router.get("/attribution", response_model=SignalAttribution)
def get_attribution(conn=Depends(get_db)):
    """ライブ成績（シグナル→実取引）と最新バックテスト期待値の比較を返す。"""
    return signal_attribution(conn)


@router.get("", response_model=list[SignalOut])
def get_signals(status: Optional[str] = None, code: Optional[str] = None,
                limit: int = 100, conn=Depends(get_db)):
    return list_signals(conn, status=status, code=code, limit=limit)


@router.get("/{signal_id}", response_model=SignalOut)
def get_one_signal(signal_id: int, conn=Depends(get_db)):
    sig = get_signal(conn, signal_id)
    if sig is None:
        raise HTTPException(status_code=404, detail=f"未登録のシグナル: {signal_id}")
    return sig


@router.post("/{signal_id}/status", response_model=SignalOut)
def set_signal_status(signal_id: int, body: SignalStatusIn, conn=Depends(get_db)):
    try:
        updated = update_signal_status(conn, signal_id, body.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if updated is None:
        raise HTTPException(status_code=404, detail=f"未登録のシグナル: {signal_id}")
    return updated
