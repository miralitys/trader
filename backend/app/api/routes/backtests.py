from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.entities import Backtest, Candle, User
from app.schemas.backtest import (
    BacktestBatchRunOut,
    BacktestBatchRunRequest,
    BacktestBatchStatsOut,
    BacktestBatchStrategyStatsOut,
    BacktestHistoryReadinessOut,
    BacktestHistoryReadinessRequest,
    BacktestOut,
    BacktestProgressOut,
    BacktestProgressStrategyOut,
    BacktestProgressTimeframeOut,
    BacktestRunRequest,
)
from app.services.backtest_service import inspect_backtest_history_readiness, rolling_24_month_window
from app.workers.celery_app import celery_app

router = APIRouter(prefix="/backtests")
DEFAULT_EXECUTION_MODEL = "CONSERVATIVE_TAKER_ONLY"
ALL_BACKTEST_STRATEGIES = [
    "StrategyBreakoutRetest",
    "StrategyPullbackToTrend",
    "MeanReversionHardStop",
    "StrategyTrendRetrace70",
]


def _normalized_backtest_params(params: dict | None) -> dict:
    payload = params.copy() if isinstance(params, dict) else {}
    payload["execution_model"] = DEFAULT_EXECUTION_MODEL
    return payload


def _create_backtest_row(
    db: Session,
    *,
    strategy: str,
    start_ts: datetime,
    end_ts: datetime,
    params: dict,
) -> Backtest:
    row = Backtest(
        strategy=strategy,
        universe_json=[],
        start_ts=start_ts,
        end_ts=end_ts,
        params_json=params,
        metrics_json={},
        equity_curve_json=[],
        status="queued",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _enqueue_backtest_task(db: Session, row: Backtest) -> None:
    async_result = celery_app.send_task("app.workers.tasks.backtest_task", args=[row.id], queue="backtests")
    params = row.params_json.copy() if isinstance(row.params_json, dict) else {}
    params["celery_task_id"] = async_result.id
    params["enqueued_at"] = datetime.now(timezone.utc).isoformat()
    row.params_json = params
    db.add(row)
    db.commit()
    db.refresh(row)


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _extract_base_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    base = metrics.get("base")
    if isinstance(base, dict):
        return base
    return {
        key: metrics.get(key)
        for key in (
            "trades",
            "winrate",
            "profit_factor",
            "expectancy",
            "expectancy_r",
            "max_drawdown_pct",
            "avg_duration_min",
            "gross_profit",
            "gross_loss",
        )
        if key in metrics
    }


@router.get("/progress", response_model=BacktestProgressOut)
def get_backtest_progress(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> BacktestProgressOut:
    generated_at = datetime.now(timezone.utc)
    start_ts, end_ts = rolling_24_month_window()

    timeframe_items: list[BacktestProgressTimeframeOut] = []
    for timeframe in ("5m", "15m", "1h"):
        candles_count = db.scalar(select(func.count(Candle.id)).where(Candle.timeframe == timeframe)) or 0
        instruments_count = db.scalar(
            select(func.count(distinct(Candle.instrument_id))).where(Candle.timeframe == timeframe)
        ) or 0
        oldest_ts = db.scalar(select(func.min(Candle.ts)).where(Candle.timeframe == timeframe))
        latest_ts = db.scalar(select(func.max(Candle.ts)).where(Candle.timeframe == timeframe))
        timeframe_items.append(
            BacktestProgressTimeframeOut(
                timeframe=timeframe,
                candles=int(candles_count),
                instruments=int(instruments_count),
                oldest_ts=oldest_ts,
                latest_ts=latest_ts,
            )
        )

    strategy_items: list[BacktestProgressStrategyOut] = []
    ready_count = 0
    for strategy in ALL_BACKTEST_STRATEGIES:
        try:
            readiness = inspect_backtest_history_readiness(
                db,
                strategy=strategy,
                start_ts=start_ts,
                end_ts=end_ts,
                params={},
            )
        except Exception as exc:
            readiness = {
                "ready": False,
                "reason": f"progress_unavailable: {exc}",
                "coverage": {"effective_ratio": 0.0, "required_ratio": 0.0},
                "universe": {"selected_top5": []},
            }

        ready = bool(readiness.get("ready"))
        if ready:
            ready_count += 1
        coverage = readiness.get("coverage") if isinstance(readiness.get("coverage"), dict) else {}
        universe = readiness.get("universe") if isinstance(readiness.get("universe"), dict) else {}
        strategy_items.append(
            BacktestProgressStrategyOut(
                strategy=strategy,
                ready=ready,
                reason=str(readiness.get("reason") or "unknown"),
                effective_ratio=float(coverage.get("effective_ratio") or 0.0),
                required_ratio=float(coverage.get("required_ratio") or 0.0),
                selected_top5=list(universe.get("selected_top5") or []),
            )
        )

    summary = {
        "ready_strategies": ready_count,
        "total_strategies": len(ALL_BACKTEST_STRATEGIES),
        "not_ready_strategies": len(ALL_BACKTEST_STRATEGIES) - ready_count,
        "all_ready": ready_count == len(ALL_BACKTEST_STRATEGIES),
    }

    return BacktestProgressOut(
        generated_at=generated_at,
        summary=summary,
        timeframes=timeframe_items,
        strategies=strategy_items,
    )


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

    row = _create_backtest_row(
        db,
        strategy=payload.strategy,
        start_ts=start_ts,
        end_ts=end_ts,
        params=_normalized_backtest_params(payload.params),
    )

    try:
        _enqueue_backtest_task(db, row)
    except Exception as exc:
        row.status = "failed"
        row.metrics_json = {"error": f"enqueue_failed: {exc}"}
        db.add(row)
        db.commit()
        db.refresh(row)
        raise HTTPException(
            status_code=503,
            detail="Failed to enqueue backtest task. Verify Redis and worker availability.",
        ) from exc
    return BacktestOut.model_validate(row)


@router.post("/run-all", response_model=BacktestBatchRunOut)
def run_backtests_for_all_strategies(
    payload: BacktestBatchRunRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> BacktestBatchRunOut:
    default_start, default_end = rolling_24_month_window()
    start_ts = payload.start_ts or default_start
    end_ts = payload.end_ts or default_end
    if start_ts >= end_ts:
        raise HTTPException(status_code=400, detail="start_ts must be earlier than end_ts")

    common_params = payload.common_params if isinstance(payload.common_params, dict) else {}
    per_strategy_params = payload.per_strategy_params if isinstance(payload.per_strategy_params, dict) else {}

    requested_strategies = payload.strategies if isinstance(payload.strategies, list) else []
    if requested_strategies:
        strategies_to_run = []
        seen: set[str] = set()
        for item in requested_strategies:
            strategy = str(item).strip()
            if not strategy or strategy in seen:
                continue
            if strategy not in ALL_BACKTEST_STRATEGIES:
                raise HTTPException(status_code=400, detail=f"Unsupported strategy: {strategy}")
            seen.add(strategy)
            strategies_to_run.append(strategy)
        if not strategies_to_run:
            raise HTTPException(status_code=400, detail="No valid strategies provided")
    else:
        strategies_to_run = list(ALL_BACKTEST_STRATEGIES)

    batch_id = (payload.batch_id or "").strip() or uuid4().hex

    rows: list[Backtest] = []
    enqueue_errors: dict[str, str] = {}

    for strategy in strategies_to_run:
        strategy_overrides = per_strategy_params.get(strategy)
        overrides = strategy_overrides if isinstance(strategy_overrides, dict) else {}
        merged_params = {
            **common_params,
            **overrides,
            "batch_id": batch_id,
            "batch_requested_start_ts": start_ts.isoformat(),
            "batch_requested_end_ts": end_ts.isoformat(),
            "batch_strategy": strategy,
        }
        row = _create_backtest_row(
            db,
            strategy=strategy,
            start_ts=start_ts,
            end_ts=end_ts,
            params=_normalized_backtest_params(merged_params),
        )
        rows.append(row)
        try:
            _enqueue_backtest_task(db, row)
        except Exception as exc:
            row.status = "failed"
            row.metrics_json = {"error": f"enqueue_failed: {exc}"}
            db.add(row)
            db.commit()
            db.refresh(row)
            enqueue_errors[strategy] = str(exc)

    return BacktestBatchRunOut(
        batch_id=batch_id,
        start_ts=start_ts,
        end_ts=end_ts,
        strategies=strategies_to_run,
        backtests=[BacktestOut.model_validate(row) for row in rows],
        enqueue_errors=enqueue_errors,
    )


@router.post("/{backtest_id}/cancel", response_model=BacktestOut)
def cancel_backtest(
    backtest_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> BacktestOut:
    row = db.scalar(select(Backtest).where(Backtest.id == backtest_id))
    if not row:
        raise HTTPException(status_code=404, detail="Backtest not found")

    if row.status in {"completed", "failed", "cancelled"}:
        raise HTTPException(status_code=409, detail=f"Backtest already {row.status}")

    params = row.params_json.copy() if isinstance(row.params_json, dict) else {}
    task_id = str(params.get("celery_task_id") or "").strip()
    params["cancel_requested_at"] = datetime.now(timezone.utc).isoformat()
    row.params_json = params

    metrics = row.metrics_json.copy() if isinstance(row.metrics_json, dict) else {}
    metrics["cancel_requested"] = True
    metrics["error"] = "cancelled_by_user"
    row.metrics_json = metrics
    row.status = "cancelled"
    db.add(row)
    db.commit()
    db.refresh(row)

    if task_id:
        try:
            celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
        except Exception as exc:
            metrics = row.metrics_json.copy() if isinstance(row.metrics_json, dict) else {}
            metrics["cancel_revoke_error"] = str(exc)
            row.metrics_json = metrics
            db.add(row)
            db.commit()
            db.refresh(row)

    return BacktestOut.model_validate(row)


@router.post("/history-readiness", response_model=BacktestHistoryReadinessOut)
def backtest_history_readiness(
    payload: BacktestHistoryReadinessRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> BacktestHistoryReadinessOut:
    default_start, default_end = rolling_24_month_window()
    start_ts = payload.start_ts or default_start
    end_ts = payload.end_ts or default_end
    if start_ts >= end_ts:
        raise HTTPException(status_code=400, detail="start_ts must be earlier than end_ts")

    readiness = inspect_backtest_history_readiness(
        db,
        strategy=payload.strategy,
        start_ts=start_ts,
        end_ts=end_ts,
        params=payload.params,
    )
    return BacktestHistoryReadinessOut.model_validate(readiness)


@router.delete("/strategy/{strategy_name}")
def clear_backtests_for_strategy(
    strategy_name: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    strategy = str(strategy_name or "").strip()
    if strategy not in ALL_BACKTEST_STRATEGIES:
        raise HTTPException(status_code=400, detail=f"Unsupported strategy: {strategy}")

    rows = db.scalars(select(Backtest).where(Backtest.strategy == strategy)).all()
    revoked = 0
    deleted = 0

    for row in rows:
        params = row.params_json if isinstance(row.params_json, dict) else {}
        task_id = str(params.get("celery_task_id") or "").strip()
        if task_id and row.status not in {"completed", "failed", "cancelled"}:
            try:
                celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
                revoked += 1
            except Exception:
                pass
        db.delete(row)
        deleted += 1

    db.commit()
    return {"strategy": strategy, "deleted": deleted, "revoked": revoked}


@router.get("/batches/{batch_id}/stats", response_model=BacktestBatchStatsOut)
def get_backtest_batch_stats(
    batch_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> BacktestBatchStatsOut:
    all_rows = db.scalars(select(Backtest).order_by(Backtest.created_at.desc()).limit(2000)).all()
    batch_rows = [
        row
        for row in all_rows
        if isinstance(row.params_json, dict) and str(row.params_json.get("batch_id", "")).strip() == batch_id
    ]
    if not batch_rows:
        raise HTTPException(status_code=404, detail="Batch not found")

    latest_by_strategy: dict[str, Backtest] = {}
    for row in batch_rows:
        if row.strategy not in ALL_BACKTEST_STRATEGIES:
            continue
        if row.strategy not in latest_by_strategy:
            latest_by_strategy[row.strategy] = row

    requested_start: datetime | None = None
    requested_end: datetime | None = None
    for row in batch_rows:
        params = row.params_json if isinstance(row.params_json, dict) else {}
        requested_start = _parse_iso_datetime(params.get("batch_requested_start_ts"))
        requested_end = _parse_iso_datetime(params.get("batch_requested_end_ts"))
        if requested_start and requested_end:
            break

    strategy_items: list[BacktestBatchStrategyStatsOut] = []
    counts: dict[str, int] = {
        "missing": 0,
        "queued": 0,
        "running": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
        "other": 0,
    }

    for strategy in ALL_BACKTEST_STRATEGIES:
        row = latest_by_strategy.get(strategy)
        if not row:
            counts["missing"] += 1
            strategy_items.append(BacktestBatchStrategyStatsOut(strategy=strategy, status="missing"))
            continue

        metrics = row.metrics_json if isinstance(row.metrics_json, dict) else {}
        status = str(row.status or "unknown")
        if status in counts:
            counts[status] += 1
        else:
            counts["other"] += 1

        strategy_items.append(
            BacktestBatchStrategyStatsOut(
                strategy=strategy,
                status=status,
                backtest_id=row.id,
                created_at=row.created_at,
                start_ts=row.start_ts,
                end_ts=row.end_ts,
                base=_extract_base_metrics(metrics),
                stress_1_5x=metrics.get("stress_1_5x") if isinstance(metrics.get("stress_1_5x"), dict) else {},
                stress_2_0x=metrics.get("stress_2_0x") if isinstance(metrics.get("stress_2_0x"), dict) else {},
                error=str(metrics.get("error")) if metrics.get("error") is not None else None,
            )
        )

    summary = {
        "total_strategies": len(ALL_BACKTEST_STRATEGIES),
        **counts,
        "all_completed": counts["completed"] == len(ALL_BACKTEST_STRATEGIES),
    }

    return BacktestBatchStatsOut(
        batch_id=batch_id,
        start_ts=requested_start,
        end_ts=requested_end,
        summary=summary,
        strategies=strategy_items,
    )


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
