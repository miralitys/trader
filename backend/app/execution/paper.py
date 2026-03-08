from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, desc, func, select
from sqlalchemy.orm import Session

from app.core.events import publish_event
from app.core.metrics import EQUITY_GAUGE, ORDERS_PLACED
from app.models.entities import Candle, EquitySnapshot, Instrument, Order, Position, Setting, Signal, Trade
from app.risk.manager import (
    InstrumentConstraints,
    RiskDecision,
    RiskManager,
    RiskParams,
    evaluate_entry_edge,
)
from app.services.market_data import get_last_price
from app.strategies.profiles import DEFAULT_INITIAL_EQUITY, get_strategy_profile, resolve_strategy_scope


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _profile_for_strategy(strategy: str | None) -> dict:
    return get_strategy_profile(strategy)


def _risk_config(strategy: str | None) -> dict:
    return _profile_for_strategy(strategy).get("risk", {})


def _fees_config(strategy: str | None) -> dict:
    return _profile_for_strategy(strategy).get("fees", {})


def _risk_params_for_strategy(strategy: str | None) -> RiskParams:
    cfg = _risk_config(strategy)
    return RiskParams(
        risk_per_trade_pct=float(cfg.get("risk_per_trade_pct", 1.0)),
        daily_loss_limit_pct=float(cfg.get("daily_loss_limit_pct", 2.0)),
        weekly_loss_limit_pct=float(cfg.get("weekly_loss_limit_pct", 5.0)),
        max_positions=int(cfg.get("max_positions", 1)),
        max_trades_per_day=int(cfg.get("max_trades_per_day", 2)),
        max_hold_hours=int(cfg.get("max_hold_hours", 72)),
        entry_ttl_minutes=int(cfg.get("entry_ttl_minutes", 60)),
        consecutive_losses_pause=int(cfg.get("consecutive_losses_pause", 2)),
        max_drawdown_pct=float(cfg.get("max_drawdown_pct", 10.0)),
        max_position_notional_pct=float(cfg.get("max_position_notional_pct", 100.0)),
        min_profit_to_cost_ratio=float(cfg.get("min_profit_to_cost_ratio", 1.2)),
    )


def _trade_strategy(trade: Trade) -> str:
    return str((trade.meta_json or {}).get("strategy") or "StrategyBreakoutRetest")


def _initial_equity(setting: Setting | None = None) -> float:
    if setting and isinstance(setting.risk_params_json, dict):
        raw = setting.risk_params_json.get("initial_equity")
        try:
            value = float(raw)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    return DEFAULT_INITIAL_EQUITY


def _latest_candle(db: Session, instrument_id: int, timeframe: str = "5m") -> Candle | None:
    return db.scalar(
        select(Candle)
        .where(Candle.instrument_id == instrument_id, Candle.timeframe == timeframe)
        .order_by(Candle.ts.desc())
        .limit(1)
    )


def _latest_price(db: Session, instrument: Instrument) -> float | None:
    redis_price = get_last_price(instrument.symbol)
    if redis_price:
        return redis_price
    candle = _latest_candle(db, instrument.id, "5m")
    return candle.close if candle else None


def _count_trades_today(db: Session, mode: str, strategy: str | None = None) -> int:
    start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = db.scalars(select(Trade).where(Trade.mode == mode, Trade.opened_at >= start)).all()
    if not strategy:
        return len(rows)
    return sum(1 for trade in rows if _trade_strategy(trade) == strategy)


def _consecutive_losses(db: Session, mode: str, strategy: str | None = None, limit: int = 10) -> int:
    day_start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = db.scalars(
        select(Trade)
        .where(
            Trade.mode == mode,
            Trade.status == "closed",
            Trade.closed_at.is_not(None),
            Trade.closed_at >= day_start,
        )
        .order_by(Trade.closed_at.desc())
        .limit(limit)
    ).all()
    losses = 0
    for trade in rows:
        if strategy and _trade_strategy(trade) != strategy:
            continue
        exit_reason = (trade.meta_json or {}).get("exit_reason")
        if trade.pnl < 0 and exit_reason == "stop":
            losses += 1
        else:
            break
    return losses


def _count_open_trades(db: Session, mode: str, strategy: str | None = None) -> int:
    rows = db.scalars(
        select(Trade).where(
            Trade.mode == mode,
            Trade.status == "open",
        )
    ).all()
    if not strategy:
        return len(rows)
    return sum(1 for trade in rows if _trade_strategy(trade) == strategy)


def _daily_weekly_loss_pct(db: Session, mode: str, equity: float) -> tuple[float, float]:
    now = _now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - timedelta(days=day_start.weekday())

    day_base = db.scalar(
        select(EquitySnapshot.equity)
        .where(EquitySnapshot.mode == mode, EquitySnapshot.ts >= day_start)
        .order_by(EquitySnapshot.ts.asc())
        .limit(1)
    )
    week_base = db.scalar(
        select(EquitySnapshot.equity)
        .where(EquitySnapshot.mode == mode, EquitySnapshot.ts >= week_start)
        .order_by(EquitySnapshot.ts.asc())
        .limit(1)
    )

    daily_loss = 0.0
    weekly_loss = 0.0
    if day_base and day_base > 0:
        daily_loss = max(0.0, ((day_base - equity) / day_base) * 100)
    if week_base and week_base > 0:
        weekly_loss = max(0.0, ((week_base - equity) / week_base) * 100)

    return daily_loss, weekly_loss


def _compute_equity(db: Session, mode: str = "paper", setting: Setting | None = None) -> tuple[float, float, float]:
    initial = _initial_equity(setting)

    realized = float(
        db.scalar(
            select(func.coalesce(func.sum(Trade.pnl), 0.0)).where(
                Trade.mode == mode,
                Trade.status == "closed",
            )
        )
        or 0.0
    )

    open_positions = db.scalars(
        select(Position).where(Position.mode == mode, Position.status == "open")
    ).all()

    unrealized = 0.0
    for pos in open_positions:
        instrument = db.scalar(select(Instrument).where(Instrument.id == pos.instrument_id))
        if not instrument:
            continue
        price = _latest_price(db, instrument)
        if not price:
            continue
        upnl = (price - pos.avg_price) * pos.qty_base
        pos.unrealized_pnl = upnl
        unrealized += upnl

    equity = initial + realized + unrealized
    prev_peak = db.scalar(
        select(func.max(EquitySnapshot.peak_equity)).where(EquitySnapshot.mode == mode)
    )
    peak = max(float(prev_peak or initial), equity)
    drawdown = 0.0 if peak <= 0 else max(0.0, ((peak - equity) / peak) * 100)

    snapshot = EquitySnapshot(mode=mode, equity=equity, ts=_now(), peak_equity=peak, drawdown_pct=drawdown)
    db.add(snapshot)
    db.commit()

    EQUITY_GAUGE.labels(mode=mode).set(equity)
    return equity, peak, drawdown


def _get_open_trade(db: Session, mode: str, instrument_id: int) -> Trade | None:
    return db.scalar(
        select(Trade).where(
            Trade.mode == mode,
            Trade.instrument_id == instrument_id,
            Trade.status == "open",
        )
    )


def _cancel_related_exit_orders(db: Session, trade_id: int) -> None:
    rows = db.scalars(
        select(Order).where(
            Order.mode == "paper",
            Order.status == "open",
        )
    ).all()
    for row in rows:
        if row.raw_json.get("trade_id") == trade_id:
            row.status = "cancelled"


def _close_position(
    db: Session,
    position: Position,
    trade: Trade,
    exit_price: float,
    reason: str,
    is_market: bool,
) -> None:
    qty = position.qty_base
    if qty <= 0:
        return

    fees_cfg = _fees_config(_trade_strategy(trade))
    fee_pct = (
        float(fees_cfg.get("taker_fee_pct", 0.4))
        if is_market
        else float(fees_cfg.get("maker_fee_pct", 0.25))
    )
    fee = qty * exit_price * (fee_pct / 100.0)
    pnl = (exit_price - trade.entry_price) * qty - fee

    order = Order(
        mode="paper",
        instrument_id=position.instrument_id,
        client_order_id=str(uuid.uuid4()),
        exchange_order_id=None,
        type="market" if is_market else "limit",
        side="sell",
        price=exit_price,
        size=qty,
        status="filled",
        raw_json={"kind": "exit", "trade_id": trade.id, "reason": reason},
    )
    db.add(order)

    trade.exit_price = exit_price
    trade.closed_at = _now()
    trade.status = "closed"
    trade.fees = float(trade.fees) + fee
    trade.pnl = float(trade.pnl) + pnl
    trade.meta_json = {**trade.meta_json, "exit_reason": reason}

    position.realized_pnl = float(position.realized_pnl) + pnl
    position.unrealized_pnl = 0.0
    position.qty_base = 0.0
    position.status = "closed"

    _cancel_related_exit_orders(db, trade.id)
    db.commit()

    event_type = "stop_hit" if reason == "stop" else "timeout_exit" if reason == "timeout" else "position_closed"
    publish_event(
        "order_filled",
        {
            "mode": "paper",
            "instrument_id": position.instrument_id,
            "trade_id": trade.id,
            "order_id": order.client_order_id,
            "reason": reason,
            "price": exit_price,
        },
    )
    publish_event(
        event_type,
        {
            "mode": "paper",
            "instrument_id": position.instrument_id,
            "trade_id": trade.id,
            "pnl": trade.pnl,
            "reason": reason,
        },
    )


def _partial_close_breakout(
    db: Session,
    position: Position,
    trade: Trade,
    partial_price: float,
) -> None:
    qty = position.qty_base * 0.5
    if qty <= 0:
        return
    fees_cfg = _fees_config(_trade_strategy(trade))
    fee_pct = float(fees_cfg.get("maker_fee_pct", 0.25))
    fee = qty * partial_price * (fee_pct / 100.0)
    pnl = (partial_price - trade.entry_price) * qty - fee

    order = Order(
        mode="paper",
        instrument_id=position.instrument_id,
        client_order_id=str(uuid.uuid4()),
        exchange_order_id=None,
        type="limit",
        side="sell",
        price=partial_price,
        size=qty,
        status="filled",
        raw_json={"kind": "partial_tp", "trade_id": trade.id},
    )
    db.add(order)

    position.qty_base = max(0.0, position.qty_base - qty)
    position.realized_pnl = float(position.realized_pnl) + pnl

    meta = trade.meta_json.copy()
    meta["partial_taken"] = True
    meta["current_stop"] = max(float(meta.get("stop", trade.entry_price)), trade.entry_price)
    meta["partial_fill_price"] = partial_price
    trade.meta_json = meta
    trade.fees = float(trade.fees) + fee
    trade.pnl = float(trade.pnl) + pnl

    db.commit()
    publish_event(
        "order_filled",
        {
            "mode": "paper",
            "instrument_id": position.instrument_id,
            "trade_id": trade.id,
            "kind": "partial_tp",
            "price": partial_price,
            "qty": qty,
        },
    )


def _place_entry_order(
    db: Session,
    signal: Signal,
    instrument: Instrument,
    decision: RiskDecision,
) -> Order:
    order = Order(
        mode="paper",
        instrument_id=instrument.id,
        client_order_id=str(uuid.uuid4()),
        exchange_order_id=None,
        type="limit",
        side="buy",
        price=signal.entry,
        size=decision.qty_base,
        status="open",
        raw_json={
            "kind": "entry",
            "signal_id": signal.id,
            "stop": signal.stop,
            "take": signal.take,
            "strategy": signal.strategy,
            "expires_at": signal.expires_at.isoformat(),
            "qty_quote": decision.qty_quote,
        },
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    ORDERS_PLACED.labels(mode="paper", type="limit").inc()
    publish_event(
        "order_placed",
        {
            "mode": "paper",
            "order_id": order.client_order_id,
            "symbol": instrument.symbol,
            "price": order.price,
            "size": order.size,
            "signal_id": signal.id,
        },
    )
    return order


def _fill_entry_order(db: Session, order: Order, signal: Signal) -> None:
    instrument = db.scalar(select(Instrument).where(Instrument.id == order.instrument_id))
    if not instrument:
        return

    order.status = "filled"

    entry_price = float(order.price or signal.entry)
    qty = float(order.size)
    qty_quote = qty * entry_price
    fees_cfg = _fees_config(signal.strategy)
    entry_fee_pct = float(fees_cfg.get("maker_fee_pct", 0.25))
    entry_fee = qty_quote * (entry_fee_pct / 100.0)

    trade = Trade(
        mode="paper",
        instrument_id=order.instrument_id,
        side="buy",
        qty_base=qty,
        qty_quote=qty_quote,
        entry_price=entry_price,
        fees=entry_fee,
        pnl=-entry_fee,
        status="open",
        order_ids_json={"entry": order.client_order_id},
        meta_json={
            "strategy": signal.strategy,
            "signal_id": signal.id,
            "stop": signal.stop,
            "take": signal.take,
            "take_targets": signal.meta_json.get("take"),
            "partial_tp": signal.meta_json.get("partial_tp"),
            "final_tp": signal.meta_json.get("final_tp", signal.take),
            "current_stop": signal.stop,
            "partial_taken": False,
            "br_trail_ema_period": signal.meta_json.get("br_trail_ema_period"),
        },
    )
    db.add(trade)
    db.flush()

    position = Position(
        mode="paper",
        instrument_id=order.instrument_id,
        side="long",
        qty_base=qty,
        avg_price=entry_price,
        unrealized_pnl=0.0,
        realized_pnl=-entry_fee,
        status="open",
    )
    db.add(position)

    tp_order = Order(
        mode="paper",
        instrument_id=order.instrument_id,
        client_order_id=str(uuid.uuid4()),
        exchange_order_id=None,
        type="limit",
        side="sell",
        price=signal.take,
        size=qty,
        status="open",
        raw_json={"kind": "tp", "trade_id": trade.id},
    )
    sl_order = Order(
        mode="paper",
        instrument_id=order.instrument_id,
        client_order_id=str(uuid.uuid4()),
        exchange_order_id=None,
        type="market",
        side="sell",
        price=signal.stop,
        size=qty,
        status="open",
        raw_json={"kind": "sl", "trade_id": trade.id, "trigger_price": signal.stop},
    )
    db.add(tp_order)
    db.add(sl_order)

    signal.status = "executed"
    db.commit()

    publish_event(
        "order_filled",
        {
            "mode": "paper",
            "symbol": instrument.symbol,
            "order_id": order.client_order_id,
            "price": entry_price,
            "size": qty,
            "signal_id": signal.id,
        },
    )
    publish_event(
        "position_opened",
        {
            "mode": "paper",
            "symbol": instrument.symbol,
            "trade_id": trade.id,
            "entry_price": entry_price,
            "qty": qty,
        },
    )


def run_paper_execution_cycle(db: Session, setting: Setting) -> dict:
    if not setting.paper_enabled:
        return {"status": "skipped", "reason": "paper_disabled"}
    if setting.kill_switch_paused:
        return {"status": "skipped", "reason": "kill_switch_paused"}

    now = _now()

    # Expire stale active signals without open entry order.
    stale_signals = db.scalars(
        select(Signal).where(Signal.status == "active", Signal.expires_at <= now)
    ).all()
    for signal in stale_signals:
        signal.status = "expired"
    db.commit()

    equity, _, drawdown_pct = _compute_equity(db, mode="paper", setting=setting)
    daily_loss_pct, weekly_loss_pct = _daily_weekly_loss_pct(db, "paper", equity)

    active_signals = db.scalars(
        select(Signal).where(Signal.status == "active").order_by(Signal.created_at.asc())
    ).all()

    placed_orders = 0
    for signal in active_signals:
        candidate_orders = db.scalars(
            select(Order).where(
                Order.mode == "paper",
                Order.instrument_id == signal.instrument_id,
                Order.status.in_(["open", "filled"]),
            )
        ).all()
        has_order = any(int(order.raw_json.get("signal_id", -1)) == signal.id for order in candidate_orders)
        if has_order:
            continue

        instrument = db.scalar(select(Instrument).where(Instrument.id == signal.instrument_id))
        if not instrument:
            signal.status = "cancelled"
            continue

        strategy = signal.strategy or "StrategyBreakoutRetest"
        fees_cfg = _fees_config(strategy)
        risk_params = _risk_params_for_strategy(strategy)
        risk_manager = RiskManager(risk_params)
        open_positions_count = _count_open_trades(db, "paper", strategy=strategy)
        trades_today = _count_trades_today(db, "paper", strategy=strategy)
        consecutive_losses = _consecutive_losses(db, "paper", strategy=strategy)

        edge_decision = evaluate_entry_edge(
            entry=float(signal.entry),
            take=float(signal.take),
            maker_fee_pct=float(fees_cfg.get("maker_fee_pct", 0.25)),
            taker_fee_pct=float(fees_cfg.get("taker_fee_pct", 0.4)),
            market_exit_slippage_pct=float(fees_cfg.get("market_exit_slippage_pct", 0.05)),
            min_profit_to_cost_ratio=risk_params.min_profit_to_cost_ratio,
        )
        if not edge_decision.allowed:
            signal.status = "cancelled"
            signal.meta_json = {
                **signal.meta_json,
                "cancel_reason": edge_decision.reason,
                "edge_check": {
                    "reward_pct": edge_decision.expected_reward_pct,
                    "cost_pct": edge_decision.expected_cost_pct,
                    "reward_to_cost_ratio": edge_decision.reward_to_cost_ratio,
                    "required_ratio": risk_params.min_profit_to_cost_ratio,
                },
            }
            db.commit()
            publish_event(
                "error",
                {
                    "component": "edge_filter",
                    "signal_id": signal.id,
                    "symbol": instrument.symbol,
                    "reason": edge_decision.reason,
                },
            )
            continue

        decision = risk_manager.assess_entry(
            equity=equity,
            entry=signal.entry,
            stop=signal.stop,
            constraints=InstrumentConstraints(
                min_size=instrument.min_size,
                size_increment=instrument.size_increment,
            ),
            current_open_positions=open_positions_count,
            trades_today=trades_today,
            daily_loss_pct=daily_loss_pct,
            weekly_loss_pct=weekly_loss_pct,
            consecutive_losses=consecutive_losses,
            drawdown_pct=drawdown_pct,
        )

        if not decision.allowed:
            signal.status = "cancelled"
            signal.meta_json = {**signal.meta_json, "cancel_reason": decision.reason}
            db.commit()
            publish_event(
                "error",
                {
                    "component": "risk_manager",
                    "signal_id": signal.id,
                    "symbol": instrument.symbol,
                    "reason": decision.reason,
                },
            )
            continue

        _place_entry_order(db, signal, instrument, decision)
        placed_orders += 1

    all_open_buy_orders = db.scalars(
        select(Order).where(
            Order.mode == "paper",
            Order.status == "open",
            Order.side == "buy",
        )
    ).all()
    open_entry_orders = [row for row in all_open_buy_orders if row.raw_json.get("kind") == "entry"]

    filled_entries = 0
    expired_entries = 0
    for order in open_entry_orders:
        expires_at = datetime.fromisoformat(order.raw_json.get("expires_at"))
        signal_id = int(order.raw_json.get("signal_id"))
        signal = db.scalar(select(Signal).where(Signal.id == signal_id))
        instrument = db.scalar(select(Instrument).where(Instrument.id == order.instrument_id))

        if not signal or not instrument:
            order.status = "cancelled"
            continue

        if now >= expires_at:
            order.status = "cancelled"
            signal.status = "expired"
            db.commit()
            expired_entries += 1
            publish_event(
                "timeout_exit",
                {
                    "mode": "paper",
                    "signal_id": signal.id,
                    "symbol": instrument.symbol,
                    "reason": "entry_ttl_expired",
                },
            )
            continue

        candle = _latest_candle(db, instrument.id, "5m")
        if not candle:
            continue

        if candle.low <= float(order.price) <= candle.high:
            _fill_entry_order(db, order, signal)
            filled_entries += 1

    open_positions = db.scalars(
        select(Position).where(Position.mode == "paper", Position.status == "open")
    ).all()

    closed_positions = 0
    partials = 0
    for position in open_positions:
        instrument = db.scalar(select(Instrument).where(Instrument.id == position.instrument_id))
        trade = _get_open_trade(db, "paper", position.instrument_id)
        if not instrument or not trade:
            continue

        candle = _latest_candle(db, instrument.id, "5m")
        if not candle:
            continue

        meta = trade.meta_json.copy()
        stop = float(meta.get("current_stop", meta.get("stop", 0.0)))
        take = float(meta.get("take", 0.0))
        strategy = meta.get("strategy", "")
        fees_cfg = _fees_config(strategy)
        slippage_pct = float(fees_cfg.get("market_exit_slippage_pct", 0.05)) / 100.0

        hold_hours = (_now() - trade.opened_at).total_seconds() / 3600
        max_hold = float(_risk_config(strategy).get("max_hold_hours", 72))

        if hold_hours >= max_hold:
            exit_price = candle.close * (1 - slippage_pct)
            _close_position(db, position, trade, exit_price, reason="timeout", is_market=True)
            closed_positions += 1
            continue

        if strategy == "StrategyBreakoutRetest" and not meta.get("partial_taken", False):
            partial_tp = float(meta.get("partial_tp") or (trade.entry_price + (trade.entry_price - stop)))
            if candle.high >= partial_tp and position.qty_base > 0:
                _partial_close_breakout(db, position, trade, partial_tp)
                partials += 1
                meta = trade.meta_json.copy()
                stop = float(meta.get("current_stop", stop))

        if strategy == "StrategyBreakoutRetest":
            trail_ema_period = max(2, int(meta.get("br_trail_ema_period") or 20))
            recent = db.scalars(
                select(Candle)
                .where(Candle.instrument_id == instrument.id, Candle.timeframe == "5m")
                .order_by(Candle.ts.desc())
                .limit(trail_ema_period)
            ).all()
            if len(recent) >= trail_ema_period:
                closes = [x.close for x in reversed(recent)]
                ema20 = closes[0]
                alpha = 2 / (trail_ema_period + 1)
                for val in closes[1:]:
                    ema20 = (val - ema20) * alpha + ema20
                trail_stop = max(stop, ema20)
                trade.meta_json = {**trade.meta_json, "current_stop": trail_stop}
                db.commit()
                stop = trail_stop

            final_tp = float(meta.get("final_tp", take))
            if candle.low <= stop:
                exit_price = stop * (1 - slippage_pct)
                _close_position(db, position, trade, exit_price, reason="stop", is_market=True)
                closed_positions += 1
            elif candle.high >= final_tp:
                _close_position(db, position, trade, final_tp, reason="take_profit", is_market=False)
                closed_positions += 1
        else:
            if candle.low <= stop:
                exit_price = stop * (1 - slippage_pct)
                _close_position(db, position, trade, exit_price, reason="stop", is_market=True)
                closed_positions += 1
            elif candle.high >= take:
                _close_position(db, position, trade, take, reason="take_profit", is_market=False)
                closed_positions += 1

    equity, peak, drawdown = _compute_equity(db, mode="paper", setting=setting)

    enabled = resolve_strategy_scope((setting.strategy_params_json or {}).get("trade_only_strategy", "both"))
    max_dd_trigger = min(float(_risk_config(name).get("max_drawdown_pct", 10.0)) for name in enabled)
    strict_action = "pause"
    if drawdown > max_dd_trigger:
        setting.strict_mode = True
        setting.kill_switch_paused = True
        db.commit()
        publish_event(
            "kill_switch",
            {
                "mode": "paper",
                "reason": "max_drawdown",
                "drawdown_pct": drawdown,
                "action": strict_action,
            },
        )

    return {
        "status": "ok",
        "placed_orders": placed_orders,
        "filled_entries": filled_entries,
        "expired_entries": expired_entries,
        "partials": partials,
        "closed_positions": closed_positions,
        "equity": equity,
        "peak": peak,
        "drawdown": drawdown,
    }
