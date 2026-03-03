from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import DEFAULT_UNIVERSE_INPUT
from app.models.entities import Backtest, Candle, Instrument, Setting
from app.services.coinbase import coinbase_client
from app.strategies.breakout_retest import generate_breakout_retest_signal
from app.strategies.pullback_trend import generate_pullback_signal
from app.strategies.types import CandleData

DEFAULT_BACKTEST_INITIAL_EQUITY = 10000.0
DEFAULT_BACKTEST_ENTRY_SLIPPAGE_PCT = 0.10
DEFAULT_BACKTEST_EXIT_SLIPPAGE_PCT = 0.10
DEFAULT_BACKTEST_STOP_SLIPPAGE_PCT = 0.20
DEFAULT_BACKTEST_TAKER_FEE_PCT = 0.60
DEFAULT_HISTORY_TARGET_COVERAGE_RATIO = 0.20
DEFAULT_HISTORY_MIN_COVERAGE_RATIO = 0.03


@dataclass
class RawTrade:
    symbol: str
    entry_ts: datetime
    exit_ts: datetime
    entry_raw: float
    exit_raw: float
    stop_price: float
    exit_reason: str
    duration_min: float


@dataclass
class SimTrade:
    symbol: str
    entry_ts: datetime
    exit_ts: datetime
    entry_exec: float
    exit_exec: float
    fees_paid: float
    pnl_quote: float
    pnl_r: float
    duration_min: float
    exit_reason: str


@dataclass
class UniverseCandidate:
    symbol: str
    product_id: str
    liquidity_score: float
    first_ts: datetime | None
    last_ts: datetime | None
    coverage_ratio: float
    selected: bool = False
    selection_reason: str = ""


def rolling_24_month_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    end = now or datetime.now(timezone.utc)
    end = end.astimezone(timezone.utc)
    try:
        start = end.replace(year=end.year - 2)
    except ValueError:
        # Handle leap-day rollover (e.g. Feb 29 -> Feb 28).
        start = end.replace(year=end.year - 2, day=28)
    return start, end


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _load_candles(
    db: Session,
    instrument_id: int,
    timeframe: str,
    start_ts: datetime,
    end_ts: datetime,
) -> list[CandleData]:
    rows = db.scalars(
        select(Candle)
        .where(
            Candle.instrument_id == instrument_id,
            Candle.timeframe == timeframe,
            Candle.ts >= start_ts,
            Candle.ts <= end_ts,
        )
        .order_by(Candle.ts.asc())
    ).all()
    return [
        CandleData(
            ts=row.ts,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
        )
        for row in rows
    ]


def _history_coverage(
    db: Session,
    instrument_id: int,
    start_ts: datetime,
    end_ts: datetime,
) -> tuple[datetime | None, datetime | None, float]:
    min_ts, max_ts = db.execute(
        select(func.min(Candle.ts), func.max(Candle.ts)).where(
            Candle.instrument_id == instrument_id,
            Candle.timeframe == "5m",
            Candle.ts <= end_ts,
        )
    ).one()

    if min_ts is None or max_ts is None:
        return None, None, 0.0

    coverage_start = max(start_ts, min_ts)
    coverage_end = min(end_ts, max_ts)
    total_seconds = max(1.0, (end_ts - start_ts).total_seconds())
    covered_seconds = max(0.0, (coverage_end - coverage_start).total_seconds())
    return min_ts, max_ts, covered_seconds / total_seconds


def _proxy_liquidity_24h(db: Session, instrument_id: int, end_ts: datetime) -> float:
    start_ts = end_ts - timedelta(hours=24)
    candles = db.scalars(
        select(Candle).where(
            Candle.instrument_id == instrument_id,
            Candle.timeframe == "5m",
            Candle.ts >= start_ts,
            Candle.ts <= end_ts,
        )
    ).all()
    return float(sum(c.close * c.volume for c in candles))


def _fetch_coinbase_products_for_backtest(input_tickers: list[str]) -> tuple[list[dict], str]:
    try:
        products = coinbase_client.get_products()
        filtered: list[dict] = []
        for product in products:
            product_id = product.get("product_id")
            base = (product.get("base_currency_id") or "").upper()
            quote = (product.get("quote_currency_id") or "").upper()
            status = str(product.get("status") or "").lower()
            trading_disabled = bool(product.get("trading_disabled", False))
            is_online = status in {"online", "active", ""} and not trading_disabled

            if not product_id:
                continue
            if quote != "USDC":
                continue
            if not is_online:
                continue
            if base not in input_tickers:
                continue

            filtered.append(product)
        return filtered, "coinbase_api"
    except Exception:
        return [], "db_fallback"


def _build_universe_candidates(
    db: Session,
    input_tickers: list[str],
    start_ts: datetime,
    end_ts: datetime,
) -> tuple[list[UniverseCandidate], str]:
    by_product = {
        row.product_id: row
        for row in db.scalars(
            select(Instrument).where(Instrument.quote == "USDC", Instrument.base.in_(input_tickers))
        ).all()
    }
    by_symbol = {row.symbol: row for row in by_product.values()}

    products, source = _fetch_coinbase_products_for_backtest(input_tickers)

    if not products:
        products = [
            {
                "product_id": row.product_id,
                "base_currency_id": row.base,
                "quote_currency_id": row.quote,
                "status": row.status,
                "trading_disabled": row.status != "online",
                "volume_24h": None,
                "price": None,
            }
            for row in by_product.values()
            if row.status == "online"
        ]

    candidates: list[UniverseCandidate] = []
    for product in products:
        product_id = product.get("product_id")
        if not product_id:
            continue

        symbol = product_id
        instrument = by_product.get(product_id) or by_symbol.get(symbol)
        if not instrument:
            continue

        quote_notional = _to_float(product.get("quote_volume_24h"))
        if quote_notional <= 0:
            quote_notional = _to_float(product.get("approximate_quote_24h_volume"))
        if quote_notional <= 0:
            base_volume = _to_float(product.get("volume_24h"))
            price = _to_float(product.get("price"))
            quote_notional = base_volume * price if base_volume > 0 and price > 0 else 0.0

        if quote_notional <= 0:
            quote_notional = _proxy_liquidity_24h(db, instrument.id, end_ts)

        first_ts, last_ts, coverage_ratio = _history_coverage(
            db,
            instrument.id,
            start_ts,
            end_ts,
        )

        candidates.append(
            UniverseCandidate(
                symbol=instrument.symbol,
                product_id=instrument.product_id,
                liquidity_score=quote_notional,
                first_ts=first_ts,
                last_ts=last_ts,
                coverage_ratio=coverage_ratio,
            )
        )

    candidates.sort(key=lambda x: x.liquidity_score, reverse=True)
    return candidates, source


def _select_top5_with_history(
    candidates: list[UniverseCandidate],
    target_coverage_ratio: float,
    min_coverage_ratio: float,
) -> list[UniverseCandidate]:
    if not candidates:
        return []

    selected: list[UniverseCandidate] = []
    remaining: list[UniverseCandidate] = []

    for candidate in candidates:
        if candidate.coverage_ratio < min_coverage_ratio:
            candidate.selection_reason = "excluded_coverage_below_floor"
            continue
        if len(selected) < 5:
            candidate.selection_reason = "liquidity_rank"
            selected.append(candidate)
        else:
            remaining.append(candidate)

    if not selected:
        return []

    for idx, item in enumerate(selected):
        if item.coverage_ratio >= target_coverage_ratio:
            continue

        replacement_idx = next(
            (
                i
                for i, candidate in enumerate(remaining)
                if candidate.coverage_ratio >= target_coverage_ratio
            ),
            None,
        )
        if replacement_idx is None:
            item.selection_reason = "kept_below_target_no_better_candidate"
            continue

        replacement = remaining.pop(replacement_idx)
        replacement.selection_reason = f"replaced_{item.symbol}_for_coverage"
        item.selection_reason = "excluded_below_target"
        selected[idx] = replacement

    for item in selected:
        item.selected = True

    return selected[:5]


def _compute_effective_common_start(
    selected: list[UniverseCandidate],
    requested_start: datetime,
    requested_end: datetime,
) -> datetime:
    starts = [max(requested_start, item.first_ts) for item in selected if item.first_ts is not None]
    if not starts:
        return requested_start

    common_start = max(starts)
    if common_start >= requested_end:
        return requested_start
    return common_start


def _simulate_raw_trades_for_symbol(
    symbol: str,
    candles_5m: list[CandleData],
    strategy: str,
    max_hold_hours: int,
) -> list[RawTrade]:
    trades: list[RawTrade] = []
    i = 60

    while i < len(candles_5m) - 2:
        window = candles_5m[: i + 1]
        signal = (
            generate_pullback_signal(window)
            if strategy == "StrategyPullbackToTrend"
            else generate_breakout_retest_signal(window)
        )

        if not signal:
            i += 1
            continue

        # Signal is computed on candle close i; earliest entry is next candle i+1.
        next_candle = candles_5m[i + 1]
        if not (next_candle.low <= signal.entry <= next_candle.high):
            i += 1
            continue

        entry_ts = next_candle.ts
        entry_raw = signal.entry
        stop = signal.stop
        take = signal.take

        max_bars = max(1, int(max_hold_hours * 12))
        j_end = min(len(candles_5m) - 1, i + max_bars)
        exit_ts = next_candle.ts
        exit_raw = next_candle.close
        exit_reason = "timeout"
        closed = False

        for j in range(i + 1, j_end + 1):
            c = candles_5m[j]
            if c.low <= stop:
                exit_raw = stop
                exit_ts = c.ts
                exit_reason = "stop"
                closed = True
                break
            if c.high >= take:
                exit_raw = take
                exit_ts = c.ts
                exit_reason = "take_profit"
                closed = True
                break

        if not closed:
            c = candles_5m[j_end]
            exit_raw = c.close
            exit_ts = c.ts
            exit_reason = "timeout"
            j = j_end

        duration_min = (exit_ts - entry_ts).total_seconds() / 60.0

        trades.append(
            RawTrade(
                symbol=symbol,
                entry_ts=entry_ts,
                exit_ts=exit_ts,
                entry_raw=entry_raw,
                exit_raw=exit_raw,
                stop_price=stop,
                exit_reason=exit_reason,
                duration_min=duration_min,
            )
        )

        i = max(i + 1, j)

    return trades


def _apply_execution_assumptions(
    raw_trades: list[RawTrade],
    taker_fee_pct: float,
    entry_slippage_pct: float,
    exit_slippage_pct: float,
    stop_slippage_pct: float,
    multiplier: float,
) -> list[SimTrade]:
    sim: list[SimTrade] = []
    for trade in raw_trades:
        fee_pct = taker_fee_pct * multiplier
        entry_slip = entry_slippage_pct * multiplier
        regular_exit_slip = exit_slippage_pct * multiplier
        stop_exit_slip = stop_slippage_pct * multiplier

        entry_exec = trade.entry_raw * (1 + entry_slip / 100.0)
        applied_exit_slip = stop_exit_slip if trade.exit_reason == "stop" else regular_exit_slip
        exit_exec = trade.exit_raw * (1 - applied_exit_slip / 100.0)

        entry_fee = entry_exec * (fee_pct / 100.0)
        exit_fee = exit_exec * (fee_pct / 100.0)
        fees_paid = entry_fee + exit_fee

        pnl_quote = (exit_exec - entry_exec) - fees_paid
        risk_unit = max(1e-8, trade.entry_raw - trade.stop_price)
        pnl_r = pnl_quote / risk_unit

        sim.append(
            SimTrade(
                symbol=trade.symbol,
                entry_ts=trade.entry_ts,
                exit_ts=trade.exit_ts,
                entry_exec=entry_exec,
                exit_exec=exit_exec,
                fees_paid=fees_paid,
                pnl_quote=pnl_quote,
                pnl_r=pnl_r,
                duration_min=trade.duration_min,
                exit_reason=trade.exit_reason,
            )
        )
    return sim


def _build_metrics(trades: list[SimTrade], initial_equity: float) -> tuple[dict, list[dict]]:
    if not trades:
        return (
            {
                "trades": 0,
                "winrate": 0.0,
                "profit_factor": 0.0,
                "expectancy": 0.0,
                "expectancy_r": 0.0,
                "max_drawdown_pct": 0.0,
                "avg_duration_min": 0.0,
                "gross_profit": 0.0,
                "gross_loss": 0.0,
            },
            [],
        )

    wins = [t for t in trades if t.pnl_quote > 0]
    losses = [t for t in trades if t.pnl_quote < 0]
    gross_profit = float(sum(t.pnl_quote for t in wins))
    gross_loss = float(abs(sum(t.pnl_quote for t in losses)))

    equity = initial_equity
    peak = equity
    max_dd = 0.0
    curve: list[dict] = []

    for trade in trades:
        equity += trade.pnl_quote
        peak = max(peak, equity)
        dd = 0.0 if peak <= 0 else ((peak - equity) / peak) * 100.0
        max_dd = max(max_dd, dd)
        curve.append({"ts": trade.exit_ts.isoformat(), "equity": equity})

    expectancy = float(sum(t.pnl_quote for t in trades) / len(trades))
    expectancy_r = float(sum(t.pnl_r for t in trades) / len(trades))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0

    metrics = {
        "trades": len(trades),
        "winrate": len(wins) / len(trades),
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "expectancy_r": expectancy_r,
        "max_drawdown_pct": max_dd,
        "avg_duration_min": sum(t.duration_min for t in trades) / len(trades),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
    }
    return metrics, curve


def run_backtest(db: Session, backtest_id: int) -> Backtest:
    backtest = db.scalar(select(Backtest).where(Backtest.id == backtest_id))
    if not backtest:
        raise RuntimeError("Backtest not found")

    backtest.status = "running"
    db.commit()

    try:
        setting = db.scalar(select(Setting).order_by(Setting.id.asc()).limit(1))
        requested_start = backtest.start_ts
        requested_end = backtest.end_ts
        params = backtest.params_json.copy()
        target_coverage_ratio = _to_float(
            params.get("history_target_coverage_ratio"),
            DEFAULT_HISTORY_TARGET_COVERAGE_RATIO,
        )
        min_coverage_ratio = _to_float(
            params.get("history_min_coverage_ratio"),
            DEFAULT_HISTORY_MIN_COVERAGE_RATIO,
        )
        target_coverage_ratio = min(max(target_coverage_ratio, 0.0), 1.0)
        min_coverage_ratio = min(max(min_coverage_ratio, 0.0), 1.0)
        if min_coverage_ratio > target_coverage_ratio:
            target_coverage_ratio = min_coverage_ratio

        input_tickers = (
            setting.universe_json.get("input_tickers", DEFAULT_UNIVERSE_INPUT)
            if setting
            else DEFAULT_UNIVERSE_INPUT
        )

        candidates, universe_source = _build_universe_candidates(
            db,
            input_tickers=[str(x).upper() for x in input_tickers],
            start_ts=requested_start,
            end_ts=requested_end,
        )
        selected = _select_top5_with_history(
            candidates=candidates,
            target_coverage_ratio=target_coverage_ratio,
            min_coverage_ratio=min_coverage_ratio,
        )
        selected_symbols = [item.symbol for item in selected]
        effective_start = _compute_effective_common_start(selected, requested_start, requested_end)

        backtest.universe_json = selected_symbols
        backtest.start_ts = effective_start

        params["period_requested_start_ts"] = requested_start.isoformat()
        params["period_requested_end_ts"] = requested_end.isoformat()
        params["period_effective_start_ts"] = effective_start.isoformat()
        params["period_effective_end_ts"] = requested_end.isoformat()
        params["history_target_coverage_ratio"] = target_coverage_ratio
        params["history_min_coverage_ratio"] = min_coverage_ratio
        backtest.params_json = params
        db.commit()

        strategy = backtest.strategy
        max_hold_hours = int(backtest.params_json.get("max_hold_hours", 72))

        raw_trades: list[RawTrade] = []
        data_availability_report: list[dict] = []

        for candidate in candidates:
            data_availability_report.append(
                {
                    "symbol": candidate.symbol,
                    "product_id": candidate.product_id,
                    "selected": candidate.selected,
                    "selection_reason": candidate.selection_reason,
                    "liquidity_score": candidate.liquidity_score,
                    "coverage_ratio_24m": round(candidate.coverage_ratio, 4),
                    "first_candle_ts": candidate.first_ts.isoformat() if candidate.first_ts else None,
                    "last_candle_ts": candidate.last_ts.isoformat() if candidate.last_ts else None,
                }
            )

        for symbol in selected_symbols:
            instrument = db.scalar(select(Instrument).where(Instrument.symbol == symbol))
            if not instrument:
                continue

            candles_5m = _load_candles(
                db,
                instrument.id,
                "5m",
                effective_start,
                requested_end,
            )
            if len(candles_5m) < 100:
                continue

            symbol_trades = _simulate_raw_trades_for_symbol(
                symbol=symbol,
                candles_5m=candles_5m,
                strategy=strategy,
                max_hold_hours=max_hold_hours,
            )
            raw_trades.extend(symbol_trades)

        raw_trades.sort(key=lambda x: x.exit_ts)

        fees_json = setting.fees_json if setting else {}
        taker_fee_pct = _to_float(fees_json.get("taker_fee_pct"), DEFAULT_BACKTEST_TAKER_FEE_PCT)
        entry_slippage_pct = _to_float(
            fees_json.get("backtest_entry_slippage_pct"),
            DEFAULT_BACKTEST_ENTRY_SLIPPAGE_PCT,
        )
        exit_slippage_pct = _to_float(
            fees_json.get("backtest_exit_slippage_pct"),
            DEFAULT_BACKTEST_EXIT_SLIPPAGE_PCT,
        )
        stop_slippage_pct = _to_float(
            fees_json.get("backtest_stop_slippage_pct"),
            DEFAULT_BACKTEST_STOP_SLIPPAGE_PCT,
        )
        initial_equity = _to_float(
            backtest.params_json.get("initial_equity"),
            DEFAULT_BACKTEST_INITIAL_EQUITY,
        )

        scenario_multipliers = {
            "base": 1.0,
            "stress_1_5x": 1.5,
            "stress_2_0x": 2.0,
        }

        scenario_metrics: dict[str, dict] = {}
        base_curve: list[dict] = []

        for name, mult in scenario_multipliers.items():
            sim_trades = _apply_execution_assumptions(
                raw_trades=raw_trades,
                taker_fee_pct=taker_fee_pct,
                entry_slippage_pct=entry_slippage_pct,
                exit_slippage_pct=exit_slippage_pct,
                stop_slippage_pct=stop_slippage_pct,
                multiplier=mult,
            )
            metrics, curve = _build_metrics(sim_trades, initial_equity)
            scenario_metrics[name] = metrics
            if name == "base":
                base_curve = curve

        assumptions = {
            "execution_model": "CONSERVATIVE_TAKER_ONLY",
            "taker_only": True,
            "period_requested": {
                "start_ts": requested_start.isoformat(),
                "end_ts": requested_end.isoformat(),
            },
            "period_effective": {
                "start_ts": effective_start.isoformat(),
                "end_ts": requested_end.isoformat(),
            },
            "universe": {
                "input_tickers": [str(x).upper() for x in input_tickers],
                "selected_top5": selected_symbols,
                "selection_source": universe_source,
                "selection_rules": [
                    "products: online + USDC quote",
                    "intersect with input tickers",
                    "rank by liquidity",
                    f"exclude coverage below {min_coverage_ratio:.2f}",
                    f"replace below target coverage {target_coverage_ratio:.2f}",
                ],
                "min_coverage_ratio": min_coverage_ratio,
                "target_coverage_ratio": target_coverage_ratio,
            },
            "fees": {
                "taker_fee_pct": taker_fee_pct,
                "stress_multipliers": [1.5, 2.0],
            },
            "slippage_pct": {
                "entry": entry_slippage_pct,
                "exit": exit_slippage_pct,
                "stop": stop_slippage_pct,
                "stress_multipliers": [1.5, 2.0],
            },
            "signal_timing": "signal_on_close_trade_next_candle",
            "lookahead": "disabled",
            "data_availability": "see metrics_json.data_availability",
        }

        base_metrics = scenario_metrics.get("base", {})
        metrics_json = {
            **base_metrics,
            "base": base_metrics,
            "stress_1_5x": scenario_metrics.get("stress_1_5x", {}),
            "stress_2_0x": scenario_metrics.get("stress_2_0x", {}),
            "assumptions": assumptions,
            "data_availability": data_availability_report,
            "raw_trades_count": len(raw_trades),
        }

        backtest.metrics_json = metrics_json
        backtest.equity_curve_json = base_curve
        backtest.status = "completed"
        db.commit()
        db.refresh(backtest)
        return backtest
    except Exception as exc:
        backtest.status = "failed"
        backtest.metrics_json = {
            "error": str(exc),
            "base": {
                "trades": 0,
                "winrate": 0.0,
                "profit_factor": 0.0,
                "expectancy": 0.0,
                "expectancy_r": 0.0,
                "max_drawdown_pct": 0.0,
                "avg_duration_min": 0.0,
            },
        }
        backtest.equity_curve_json = []
        db.commit()
        db.refresh(backtest)
        return backtest
