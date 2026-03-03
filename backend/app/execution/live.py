from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.events import publish_event
from app.models.entities import Instrument, Order, Position, Setting, Signal, Trade
from app.risk.manager import InstrumentConstraints, RiskManager, RiskParams
from app.services.coinbase import CoinbaseCredentials, coinbase_client
from app.services.market_data import get_last_price


def _load_live_credentials(setting: Setting) -> CoinbaseCredentials | None:
    from app.core.config import settings as app_settings
    from app.core.secrets import load_coinbase_credentials

    key, secret = load_coinbase_credentials(
        setting.coinbase_api_key_enc,
        setting.coinbase_api_secret_enc,
    )
    if not key:
        key = app_settings.coinbase_api_key
    if not secret:
        secret = app_settings.coinbase_api_secret
    passphrase = app_settings.coinbase_api_passphrase

    if not key or not secret:
        return None
    return CoinbaseCredentials(api_key=key, api_secret=secret, passphrase=passphrase)


def _place_live_entry_order(
    db: Session,
    credentials: CoinbaseCredentials,
    setting: Setting,
    signal: Signal,
    instrument: Instrument,
    qty_base: float,
) -> None:
    client_order_id = str(uuid.uuid4())
    response = coinbase_client.place_limit_order(
        credentials=credentials,
        product_id=instrument.product_id,
        side="buy",
        size=f"{qty_base:.8f}",
        price=f"{signal.entry:.8f}",
        client_order_id=client_order_id,
        post_only=True,
    )

    exchange_order_id = (
        response.get("success_response", {}).get("order_id")
        or response.get("order_id")
        or ""
    )

    order = Order(
        mode="live",
        instrument_id=instrument.id,
        client_order_id=client_order_id,
        exchange_order_id=exchange_order_id or None,
        type="limit",
        side="buy",
        price=signal.entry,
        size=qty_base,
        status="open",
        raw_json={
            "kind": "entry",
            "signal_id": signal.id,
            "stop": signal.stop,
            "take": signal.take,
            "strategy": signal.strategy,
            "response": response,
        },
    )
    db.add(order)
    signal.status = "executed"
    db.commit()

    publish_event(
        "order_placed",
        {
            "mode": "live",
            "symbol": instrument.symbol,
            "client_order_id": client_order_id,
            "exchange_order_id": exchange_order_id,
            "price": signal.entry,
            "size": qty_base,
        },
    )


def _sync_live_fills(db: Session, credentials: CoinbaseCredentials) -> None:
    open_orders = db.scalars(
        select(Order).where(Order.mode == "live", Order.status.in_(["open", "partially_filled"]))
    ).all()

    for order in open_orders:
        if not order.exchange_order_id:
            continue
        exchange_order = coinbase_client.get_order(credentials, order.exchange_order_id)
        status = (
            exchange_order.get("order", {}).get("status")
            or exchange_order.get("status")
            or "UNKNOWN"
        ).lower()

        if status in ("filled", "done"):
            order.status = "filled"
            if order.side == "buy":
                existing_trade = db.scalar(
                    select(Trade).where(
                        Trade.mode == "live",
                        Trade.status == "open",
                        Trade.instrument_id == order.instrument_id,
                    )
                )
                if not existing_trade:
                    price = float(order.price or 0.0)
                    qty = float(order.size)
                    trade = Trade(
                        mode="live",
                        instrument_id=order.instrument_id,
                        side="buy",
                        qty_base=qty,
                        qty_quote=qty * price,
                        entry_price=price,
                        fees=0.0,
                        pnl=0.0,
                        status="open",
                        order_ids_json={"entry": order.client_order_id},
                        meta_json={
                            "stop": order.raw_json.get("stop"),
                            "take": order.raw_json.get("take"),
                            "strategy": order.raw_json.get("strategy"),
                        },
                    )
                    db.add(trade)
                    db.flush()

                    position = Position(
                        mode="live",
                        instrument_id=order.instrument_id,
                        side="long",
                        qty_base=qty,
                        avg_price=price,
                        unrealized_pnl=0.0,
                        realized_pnl=0.0,
                        status="open",
                    )
                    db.add(position)
                    db.commit()
                    publish_event(
                        "position_opened",
                        {
                            "mode": "live",
                            "instrument_id": order.instrument_id,
                            "qty": qty,
                            "entry_price": price,
                        },
                    )
        elif status in ("cancelled", "rejected", "failed"):
            order.status = "cancelled"

    db.commit()


def run_live_execution_cycle(db: Session, setting: Setting) -> dict:
    if not setting.live_enabled:
        return {"status": "skipped", "reason": "live_disabled"}
    if not setting.live_confirmed:
        return {"status": "skipped", "reason": "live_not_confirmed"}
    if setting.kill_switch_paused:
        return {"status": "skipped", "reason": "kill_switch_paused"}

    credentials = _load_live_credentials(setting)
    if not credentials:
        setting.kill_switch_paused = True
        db.commit()
        publish_event(
            "kill_switch",
            {"mode": "live", "reason": "missing_credentials", "action": "pause"},
        )
        return {"status": "stopped", "reason": "missing_credentials"}

    try:
        # Keep local state in sync with exchange first.
        _sync_live_fills(db, credentials)

        # Risk-aware order placement for new active signals.
        risk_params = RiskParams(
            risk_per_trade_pct=float(setting.risk_params_json.get("risk_per_trade_pct", 1.0)),
            daily_loss_limit_pct=float(setting.risk_params_json.get("daily_loss_limit_pct", 2.0)),
            weekly_loss_limit_pct=float(setting.risk_params_json.get("weekly_loss_limit_pct", 5.0)),
            max_positions=int(setting.risk_params_json.get("max_positions", 1)),
            max_trades_per_day=int(setting.risk_params_json.get("max_trades_per_day", 2)),
            max_hold_hours=int(setting.risk_params_json.get("max_hold_hours", 72)),
            entry_ttl_minutes=int(setting.risk_params_json.get("entry_ttl_minutes", 60)),
            consecutive_losses_pause=int(setting.risk_params_json.get("consecutive_losses_pause", 2)),
            max_drawdown_pct=float(setting.risk_params_json.get("max_drawdown_pct", 10.0)),
            max_position_notional_pct=float(
                setting.risk_params_json.get("max_position_notional_pct", 100.0)
            ),
        )
        risk = RiskManager(risk_params)

        open_positions = int(
            db.scalar(select(func.count(Position.id)).where(Position.mode == "live", Position.status == "open"))
            or 0
        )
        trades_today = int(
            db.scalar(
                select(func.count(Trade.id)).where(
                    Trade.mode == "live",
                    Trade.opened_at >= datetime.now(timezone.utc).replace(
                        hour=0, minute=0, second=0, microsecond=0
                    ),
                )
            )
            or 0
        )

        active_signals = db.scalars(
            select(Signal).where(Signal.status == "active").order_by(Signal.created_at.asc())
        ).all()

        placed = 0
        for signal in active_signals:
            instrument = db.scalar(select(Instrument).where(Instrument.id == signal.instrument_id))
            if not instrument:
                continue

            has_live_order = db.scalar(
                select(Order).where(
                    Order.mode == "live",
                    Order.instrument_id == instrument.id,
                    Order.status.in_(["open", "filled"]),
                )
            )
            if has_live_order:
                continue

            mark = get_last_price(instrument.symbol) or signal.entry
            equity_est = float(setting.risk_params_json.get("initial_equity", 10000.0))

            decision = risk.assess_entry(
                equity=equity_est,
                entry=signal.entry,
                stop=signal.stop,
                constraints=InstrumentConstraints(
                    min_size=instrument.min_size,
                    size_increment=instrument.size_increment,
                ),
                current_open_positions=open_positions,
                trades_today=trades_today,
                daily_loss_pct=0.0,
                weekly_loss_pct=0.0,
                consecutive_losses=0,
                drawdown_pct=0.0,
            )
            if not decision.allowed:
                signal.meta_json = {**signal.meta_json, "live_reject_reason": decision.reason}
                db.commit()
                continue

            _place_live_entry_order(
                db=db,
                credentials=credentials,
                setting=setting,
                signal=signal,
                instrument=instrument,
                qty_base=decision.qty_base,
            )
            placed += 1
            open_positions += 1
            trades_today += 1

        return {"status": "ok", "placed": placed}
    except Exception as exc:
        setting.kill_switch_paused = True
        db.commit()
        publish_event(
            "kill_switch",
            {
                "mode": "live",
                "reason": "live_execution_error",
                "error": str(exc),
                "action": "pause",
            },
        )
        return {"status": "stopped", "error": str(exc)}
