from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.events import publish_event
from app.models.entities import Order, Position, Setting
from app.services.coinbase import CoinbaseCredentials, coinbase_client
from app.services.market_data import get_last_sync_info


def run_reconciliation_cycle(
    db: Session,
    setting: Setting,
    credentials: CoinbaseCredentials | None,
) -> dict:
    if not setting.live_enabled:
        return {"status": "skipped", "reason": "live_disabled"}

    sync_ts, delay = get_last_sync_info()
    if setting.risk_params_json.get("kill_switch_on_data_error", True):
        if delay is None or delay > 600:
            setting.kill_switch_paused = True
            db.commit()
            publish_event(
                "data_delay",
                {
                    "mode": "live",
                    "delay_seconds": delay,
                    "last_sync": sync_ts.isoformat() if sync_ts else None,
                },
            )
            publish_event(
                "kill_switch",
                {"mode": "live", "reason": "data_delay", "delay_seconds": delay},
            )
            return {"status": "stopped", "reason": "data_delay"}

    if not credentials:
        if setting.risk_params_json.get("kill_switch_on_reconciliation_error", True):
            setting.kill_switch_paused = True
            db.commit()
            publish_event("kill_switch", {"mode": "live", "reason": "missing_credentials"})
        return {"status": "stopped", "reason": "missing_credentials"}

    try:
        exchange_open_orders = coinbase_client.list_open_orders(credentials)
        local_open_orders = int(
            db.scalar(select(func.count(Order.id)).where(Order.mode == "live", Order.status == "open"))
            or 0
        )

        mismatch = abs(len(exchange_open_orders) - local_open_orders)
        if mismatch > 0 and setting.risk_params_json.get("kill_switch_on_reconciliation_error", True):
            setting.kill_switch_paused = True
            db.commit()
            publish_event(
                "kill_switch",
                {
                    "mode": "live",
                    "reason": "open_order_mismatch",
                    "exchange_open_orders": len(exchange_open_orders),
                    "local_open_orders": local_open_orders,
                },
            )
            return {"status": "stopped", "reason": "open_order_mismatch"}

        open_positions = int(
            db.scalar(select(func.count(Position.id)).where(Position.mode == "live", Position.status == "open"))
            or 0
        )
        return {
            "status": "ok",
            "exchange_open_orders": len(exchange_open_orders),
            "local_open_orders": local_open_orders,
            "open_positions": open_positions,
        }
    except Exception as exc:
        if setting.risk_params_json.get("kill_switch_on_reconciliation_error", True):
            setting.kill_switch_paused = True
            db.commit()
            publish_event(
                "kill_switch",
                {
                    "mode": "live",
                    "reason": "reconciliation_exception",
                    "error": str(exc),
                },
            )
        return {"status": "error", "error": str(exc)}
