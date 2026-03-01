from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.entities import Backtest, User
from app.schemas.backtest import BacktestOut, BacktestRunRequest
from app.services.backtest_service import rolling_24_month_window
from app.workers.tasks import backtest_task

router = APIRouter(prefix="/backtests")


@router.post("/run", response_model=BacktestOut)
def run_backtest(
    payload: BacktestRunRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> BacktestOut:
    default_start, default_end = rolling_24_month_window()
    start_ts = payload.start_ts or default_start
    end_ts = payload.end_ts or default_end
    if start_ts >= end_ts:
        raise HTTPException(status_code=400, detail="start_ts must be earlier than end_ts")

    row = Backtest(
        strategy=payload.strategy,
        universe_json=[],
        start_ts=start_ts,
        end_ts=end_ts,
        params_json={**payload.params, "execution_model": "CONSERVATIVE_TAKER_ONLY"},
        metrics_json={},
        equity_curve_json=[],
        status="queued",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    backtest_task.delay(row.id)
    return BacktestOut.model_validate(row)


@router.get("", response_model=list[BacktestOut])
def list_backtests(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[BacktestOut]:
    rows = db.scalars(select(Backtest).order_by(Backtest.created_at.desc()).limit(200)).all()
    return [BacktestOut.model_validate(row) for row in rows]


@router.get("/{backtest_id}", response_model=BacktestOut)
def get_backtest(
    backtest_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> BacktestOut:
    row = db.scalar(select(Backtest).where(Backtest.id == backtest_id))
    if not row:
        raise HTTPException(status_code=404, detail="Backtest not found")
    return BacktestOut.model_validate(row)


@router.get("/{backtest_id}/export")
def export_backtest(
    backtest_id: int,
    fmt: str = Query("json", pattern="^(json|csv)$"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    row = db.scalar(select(Backtest).where(Backtest.id == backtest_id))
    if not row:
        raise HTTPException(status_code=404, detail="Backtest not found")

    if fmt == "json":
        return {
            "id": row.id,
            "strategy": row.strategy,
            "metrics": row.metrics_json,
            "equity_curve": row.equity_curve_json,
            "params": row.params_json,
            "status": row.status,
        }

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ts", "equity"])
    for point in row.equity_curve_json:
        writer.writerow([point.get("ts"), point.get("equity")])

    return PlainTextResponse(
        content=output.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=backtest_{row.id}.csv",
        },
    )
