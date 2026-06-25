"""シグナル追跡のAPI（実取引フィードバックループ）"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_db, auto_expire
from api.schemas import (
    SignalOut, SignalStatusIn, SignalAttribution, ScoreCalibrationBucket,
    SignalFillIn, TradeOut,
)
from data.repository import (
    list_signals, get_signal, update_signal_status, signal_attribution,
    score_calibration, list_trades, add_trade, sync_holding_from_trades,
    get_signal_by_message_id,
)

router = APIRouter(prefix="/api/signals", tags=["signals"])


@router.get("/attribution", response_model=SignalAttribution)
def get_attribution(conn=Depends(get_db)):
    """ライブ成績（シグナル→実取引）と最新バックテスト期待値の比較を返す。"""
    auto_expire(conn)  # 期限切れOPENを反映してから集計（約定率を正しく出す）
    return signal_attribution(conn)


@router.get("/calibration", response_model=list[ScoreCalibrationBucket])
def get_calibration(conn=Depends(get_db)):
    """score バケット別の予測的中度（評価済み signal_outcomes の集計）。"""
    return score_calibration(conn)


@router.get("", response_model=list[SignalOut])
def get_signals(status: Optional[str] = None, code: Optional[str] = None,
                limit: int = 100, conn=Depends(get_db)):
    auto_expire(conn)  # 読み取り前に期限切れOPENを EXPIRED へ自動遷移
    return list_signals(conn, status=status, code=code, limit=limit)


@router.get("/by-message/{message_id}", response_model=SignalOut)
def get_signal_for_message(message_id: str, conn=Depends(get_db)):
    """Discord メッセージID からシグナルを逆引きする（Bot のリアクション処理用）。"""
    sig = get_signal_by_message_id(conn, message_id)
    if sig is None:
        raise HTTPException(status_code=404, detail=f"未登録のメッセージID: {message_id}")
    return sig


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


@router.get("/{signal_id}/trades", response_model=list[TradeOut])
def get_signal_trades(signal_id: int, conn=Depends(get_db)):
    """シグナルに紐付く約定（建玉・決済）の一覧を返す。"""
    if get_signal(conn, signal_id) is None:
        raise HTTPException(status_code=404, detail=f"未登録のシグナル: {signal_id}")
    return list_trades(conn, signal_id=signal_id)


def _record_signal_trade(conn, signal_id: int, side: str, body: SignalFillIn) -> dict:
    """シグナルに紐付く約定を1件記録する（建玉=BUY / 決済=SELL 共通）。

    取引はシグナルの code/name を引き継いで trades に追加する。add_trade が
    status/realized_r を再計算し、取引ルーターと同様に保有(holdings)も同期する。
    """
    sig = get_signal(conn, signal_id)
    if sig is None:
        raise HTTPException(status_code=404, detail=f"未登録のシグナル: {signal_id}")
    try:
        add_trade(
            conn, code=sig["code"], side=side, shares=body.shares, price=body.price,
            traded_at=body.traded_at, name=sig.get("name"), fee=body.fee,
            signal_id=signal_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    sync_holding_from_trades(conn, sig["code"])
    return get_signal(conn, signal_id)


@router.post("/{signal_id}/fill", response_model=SignalOut)
def record_fill(signal_id: int, body: SignalFillIn, conn=Depends(get_db)):
    """建玉（買付約定）を記録する。全量約定で status は TAKEN になる。"""
    return _record_signal_trade(conn, signal_id, "BUY", body)


@router.post("/{signal_id}/close", response_model=SignalOut)
def record_close(signal_id: int, body: SignalFillIn, conn=Depends(get_db)):
    """決済（売付約定）を記録する。全株決済で status は CLOSED になり実現Rが計算される。"""
    return _record_signal_trade(conn, signal_id, "SELL", body)
