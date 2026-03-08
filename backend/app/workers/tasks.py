from __future__ import annotations

import time

from celery import shared_task
from sqlalchemy import select

from app.core.metrics import TASK_RUNTIME
from app.db.session import SessionLocal
from app.execution.live import run_live_execution_cycle
from app.execution.paper import run_paper_execution_cycle
from app.execution.reconciliation import run_reconciliation_cycle
from app.models.entities import Setting
from app.services.backtest_service import BACKTEST_STALE_TIMEOUT_MINUTES, fail_stale_backtests, run_backtest
from app.services.coinbase import CoinbaseCredentials
from app.services.market_data import backfill_history, ingest_candles, sync_instruments
from app.services.strategy_runner import expire_stale_signals, run_strategy_cycle
from app.services.universe import recompute_universe


def _timed(task_name: str):
    class _Timer:
        def __enter__(self):
            self.start = time.perf_counter()
            return self

        def __exit__(self, exc_type, exc, tb):
            TASK_RUNTIME.labels(task=task_name).observe(time.perf_counter() - self.start)

    return _Timer()


def _get_setting(db) -> Setting | None:
    return db.scalar(select(Setting).order_by(Setting.id.asc()).limit(1))


@shared_task(name="app.workers.tasks.ingest_market_data_task")
def ingest_market_data_task() -> dict:
    with _timed("ingest_market_data"):
        db = SessionLocal()
        try:
            sync_instruments(db)
            setting = _get_setting(db)
            symbols = setting.universe_json.get("top_symbols", []) if setting else []
            if not symbols and setting:
                universe = recompute_universe(db, setting)
                symbols = universe.get("top_symbols", [])
            if not symbols:
                return {"status": "skipped", "reason": "empty_universe"}
            return ingest_candles(db, symbols)
        finally:
            db.close()


@shared_task(name="app.workers.tasks.universe_selector_task")
def universe_selector_task() -> dict:
    with _timed("universe_selector"):
        db = SessionLocal()
        try:
            setting = _get_setting(db)
            if not setting:
                return {"status": "skipped", "reason": "no_settings"}
            result = recompute_universe(db, setting)
            return {
                "status": "ok",
                "top_symbols": result.get("top_symbols", []),
                "count": len(result.get("top_symbols", [])),
            }
        finally:
            db.close()


@shared_task(name="app.workers.tasks.backfill_history_task")
def backfill_history_task() -> dict:
    with _timed("backfill_history"):
        db = SessionLocal()
        try:
            sync_instruments(db)
            setting = _get_setting(db)
            symbols = setting.universe_json.get("top_symbols", []) if setting else []
            if not symbols and setting:
                universe = recompute_universe(db, setting)
                symbols = universe.get("top_symbols", [])
            return backfill_history(db, symbols=symbols)
        finally:
            db.close()


@shared_task(name="app.workers.tasks.strategy_runner_task")
def strategy_runner_task() -> dict:
    with _timed("strategy_runner"):
        db = SessionLocal()
        try:
            setting = _get_setting(db)
            if not setting:
                return {"status": "skipped", "reason": "no_settings"}
            return run_strategy_cycle(db, setting)
        finally:
            db.close()


@shared_task(name="app.workers.tasks.signal_expiry_task")
def signal_expiry_task() -> dict:
    with _timed("signal_expiry"):
        db = SessionLocal()
        try:
            expired = expire_stale_signals(db)
            return {"expired": expired}
        finally:
            db.close()


@shared_task(name="app.workers.tasks.paper_execution_task")
def paper_execution_task() -> dict:
    with _timed("paper_execution"):
        db = SessionLocal()
        try:
            setting = _get_setting(db)
            if not setting:
                return {"status": "skipped", "reason": "no_settings"}
            return run_paper_execution_cycle(db, setting)
        finally:
            db.close()


@shared_task(name="app.workers.tasks.live_execution_task")
def live_execution_task() -> dict:
    with _timed("live_execution"):
        db = SessionLocal()
        try:
            setting = _get_setting(db)
            if not setting:
                return {"status": "skipped", "reason": "no_settings"}
            return run_live_execution_cycle(db, setting)
        finally:
            db.close()


@shared_task(name="app.workers.tasks.reconciliation_task")
def reconciliation_task() -> dict:
    with _timed("reconciliation"):
        db = SessionLocal()
        try:
            setting = _get_setting(db)
            if not setting:
                return {"status": "skipped", "reason": "no_settings"}

            credentials = None
            if setting.live_enabled:
                from app.core.secrets import load_coinbase_credentials
                from app.core.config import settings as app_settings

                api_key, api_secret = load_coinbase_credentials(
                    setting.coinbase_api_key_enc,
                    setting.coinbase_api_secret_enc,
                )
                api_key = api_key or app_settings.coinbase_api_key
                api_secret = api_secret or app_settings.coinbase_api_secret
                if api_key and api_secret:
                    credentials = CoinbaseCredentials(
                        api_key=api_key,
                        api_secret=api_secret,
                        passphrase=app_settings.coinbase_api_passphrase,
                    )

            return run_reconciliation_cycle(db, setting, credentials)
        finally:
            db.close()


@shared_task(name="app.workers.tasks.backtest_task")
def backtest_task(backtest_id: int) -> dict:
    with _timed("backtest"):
        db = SessionLocal()
        try:
            backtest = run_backtest(db, backtest_id)
            return {
                "status": backtest.status,
                "backtest_id": backtest.id,
                "metrics": backtest.metrics_json,
            }
        finally:
            db.close()


@shared_task(name="app.workers.tasks.backtest_reaper_task")
def backtest_reaper_task() -> dict:
    with _timed("backtest_reaper"):
        db = SessionLocal()
        try:
            return fail_stale_backtests(db, stale_minutes=BACKTEST_STALE_TIMEOUT_MINUTES)
        finally:
            db.close()
