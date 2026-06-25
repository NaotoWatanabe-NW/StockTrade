"""バックテスト実行履歴のAPI（履歴の閲覧＋Webからの実行）"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from api.deps import get_db
from api.schemas import BacktestRunOut, BacktestRunRequest
from data.db import get_connection
from data.repository import (
    list_backtest_runs, get_backtest_run,
    create_backtest_job, finish_backtest_run, fail_backtest_run,
)

router = APIRouter(prefix="/api/backtest", tags=["backtest"])
log = logging.getLogger(__name__)


@router.get("", response_model=list[BacktestRunOut])
def get_backtest_runs(limit: int = 20, conn=Depends(get_db)):
    """直近 limit 件のバックテスト実行履歴を新しい順で返す（equity_curve は含まない）。"""
    return list_backtest_runs(conn, limit=limit)


@router.get("/defaults")
def get_backtest_defaults():
    """調整可能パラメータの現在値（Webフォームの初期値）を返す。"""
    from backtest.runner import current_param_defaults
    return current_param_defaults()


def _execute_backtest_job(run_id: int, req: BacktestRunRequest) -> None:
    """バックグラウンドでバックテストを実行し、結果をジョブ行へ書き込む。

    リクエスト接続は応答後に閉じられるため、ここでは専用接続を開いて使う。
    """
    from backtest.runner import run_backtest
    conn = get_connection()
    try:
        result = run_backtest(
            universe=req.universe, regime=req.regime, no_partial_tp=req.no_partial_tp,
            min_score=req.min_score, param_overrides=req.params or None,
            no_cache=False, save=False, conn=conn,
        )
        finish_backtest_run(conn, run_id, result["metrics"], result["params"])
    except Exception as e:  # noqa: BLE001 — 失敗はジョブ行に記録して握りつぶす
        log.exception("バックテストジョブ失敗 (run_id=%s)", run_id)
        try:
            fail_backtest_run(conn, run_id, f"{type(e).__name__}: {e}")
        except Exception:
            pass
    finally:
        conn.close()


@router.post("/run", response_model=BacktestRunOut, status_code=202)
def start_backtest_run(req: BacktestRunRequest, background: BackgroundTasks, conn=Depends(get_db)):
    """バックテストをバックグラウンドで開始し、status='running' のジョブ行を即返す。

    Web はこの id（または一覧）をポーリングし、status が done/error になったら結果を表示する。
    パラメータ名のタイポは実行前に 400 で弾く。
    """
    from backtest.runner import _apply_param_overrides
    try:
        # 未知パラメータの早期検出（捨て dict に対して適用してみる）
        _apply_param_overrides(req.params or None, {
            "trade_plan": {}, "exit": {}, "backtest": {}, "screening": {},
            "scoring": {}, "regime": {} if req.regime else None,
        })
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    run_id = create_backtest_job(conn, req.universe, {"request": req.model_dump()})
    background.add_task(_execute_backtest_job, run_id, req)
    return get_backtest_run(conn, run_id)


@router.get("/{run_id}", response_model=BacktestRunOut)
def get_backtest_run_by_id(run_id: int, conn=Depends(get_db)):
    """指定 id の詳細を返す（equity_curve を含む）。"""
    row = get_backtest_run(conn, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"backtest run {run_id} not found")
    return row
