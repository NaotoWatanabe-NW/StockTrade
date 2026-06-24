"""損益集計のAPI（約定履歴ベースの実現損益）"""

from fastapi import APIRouter, Depends

from api.deps import get_db
from api.schemas import PnlResponse
from config import TAX_CONFIG
from data.repository import realized_pnl, realized_pnl_summary

router = APIRouter(prefix="/api/pnl", tags=["pnl"])


@router.get("", response_model=PnlResponse)
def get_realized_pnl(conn=Depends(get_db)):
    """銘柄ごとの実現損益（平均取得単価ベース）と、税引後の通貨別サマリ。"""
    rows = realized_pnl(conn)
    summary = realized_pnl_summary(rows, TAX_CONFIG["capital_gains_rate"])
    return {"rows": rows, "summary": summary}
