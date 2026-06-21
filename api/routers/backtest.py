"""バックテスト実行履歴のAPI"""

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_db
from api.schemas import BacktestRunOut
from data.repository import list_backtest_runs, get_backtest_run

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


@router.get("", response_model=list[BacktestRunOut])
def get_backtest_runs(limit: int = 20, conn=Depends(get_db)):
    """直近 limit 件のバックテスト実行履歴を新しい順で返す（equity_curve は含まない）。"""
    return list_backtest_runs(conn, limit=limit)


@router.get("/{run_id}", response_model=BacktestRunOut)
def get_backtest_run_by_id(run_id: int, conn=Depends(get_db)):
    """指定 id の詳細を返す（equity_curve を含む）。"""
    row = get_backtest_run(conn, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"backtest run {run_id} not found")
    return row
