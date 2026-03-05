from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import product
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import DEFAULT_UNIVERSE_INPUT
from app.models.entities import Backtest, Candle, Instrument, Setting
from app.services.coinbase import coinbase_client
from app.strategies.breakout_retest import generate_breakout_retest_signal
from app.strategies.indicators import atr, ema
from app.strategies.mean_reversion_hard_stop import generate_mean_reversion_hard_stop_signal
from app.strategies.profiles import DEFAULT_INITIAL_EQUITY, get_strategy_profile
from app.strategies.pullback_trend import generate_pullback_signal
from app.strategies.trend_retrace_70 import generate_trend_retrace_70_signal
from app.strategies.types import CandleData

DEFAULT_BACKTEST_INITIAL_EQUITY = DEFAULT_INITIAL_EQUITY
DEFAULT_BACKTEST_ENTRY_SLIPPAGE_PCT = 0.10
DEFAULT_BACKTEST_EXIT_SLIPPAGE_PCT = 0.10
DEFAULT_BACKTEST_STOP_SLIPPAGE_PCT = 0.20
DEFAULT_BACKTEST_TAKER_FEE_PCT = 0.60
DEFAULT_HISTORY_TARGET_COVERAGE_RATIO = 0.20
DEFAULT_HISTORY_MIN_COVERAGE_RATIO = 0.03
DEFAULT_HISTORY_REQUIRED_COVERAGE_RATIO = 0.20
BACKTEST_STALE_TIMEOUT_MINUTES = 60
STRATEGY_BREAKOUT_RETEST_2 = "StrategyBreakoutRetest 2"
BREAKOUT_RETEST_2_MIN_COVERAGE_RATIO = 0.005
BREAKOUT_RETEST_2_TARGET_COVERAGE_RATIO = 0.005
BREAKOUT_RETEST_2_EXTRA_TICKERS = ["BTC", "ETH", "SOL", "XRP", "ADA"]
BREAKOUT_RETEST_2_TARGET_WINRATE = 0.70
BREAKOUT_RETEST_2_TARGET_PF = 1.00
BREAKOUT_RETEST_2_MIN_TRADES_FOR_TARGET = 20
SUPPORTED_BACKTEST_STRATEGIES = {
    "StrategyBreakoutRetest",
    "StrategyPullbackToTrend",
    "MeanReversionHardStop",
    "StrategyTrendRetrace70",
}


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


def fail_stale_backtests(db: Session, *, stale_minutes: int = BACKTEST_STALE_TIMEOUT_MINUTES) -> dict[str, int]:
    stale_minutes = max(1, int(stale_minutes))
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)

    stale_rows = db.scalars(
        select(Backtest).where(Backtest.status == "running", Backtest.created_at <= cutoff)
    ).all()

    marked = 0
    for row in stale_rows:
        metrics = row.metrics_json if isinstance(row.metrics_json, dict) else {}
        metrics = metrics.copy()
        metrics["error"] = f"stale_timeout: running for over {stale_minutes} minutes"
        row.metrics_json = metrics
        row.status = "failed"
        marked += 1

    if marked:
        db.commit()

    return {"stale_marked_failed": marked, "stale_minutes": stale_minutes}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _format_ratio(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _normalize_input_tickers(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        ticker = str(item).strip().upper()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        normalized.append(ticker)
    return normalized


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _resolve_runtime_strategy_from_values(requested_strategy: str, params: dict[str, Any]) -> str:
    requested_base = params.get("strategy_base_strategy")
    if isinstance(requested_base, str) and requested_base in SUPPORTED_BACKTEST_STRATEGIES:
        return requested_base

    if requested_strategy == STRATEGY_BREAKOUT_RETEST_2:
        return "StrategyBreakoutRetest"

    if requested_strategy in SUPPORTED_BACKTEST_STRATEGIES:
        return requested_strategy

    return "StrategyBreakoutRetest"


def _resolve_runtime_strategy(backtest: Backtest) -> str:
    return _resolve_runtime_strategy_from_values(backtest.strategy, backtest.params_json or {})


def _effective_coverage_ratio(
    requested_start: datetime,
    requested_end: datetime,
    effective_start: datetime,
) -> float:
    total_seconds = max(1.0, (requested_end - requested_start).total_seconds())
    covered_start = max(requested_start, effective_start)
    covered_seconds = max(0.0, (requested_end - covered_start).total_seconds())
    return covered_seconds / total_seconds


def _build_data_availability_report(candidates: list[UniverseCandidate]) -> list[dict[str, Any]]:
    report: list[dict[str, Any]] = []
    for candidate in candidates:
        report.append(
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
    return report


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
    start_ts = _as_utc(start_ts) or start_ts
    end_ts = _as_utc(end_ts) or end_ts
    min_ts, max_ts = db.execute(
        select(func.min(Candle.ts), func.max(Candle.ts)).where(
            Candle.instrument_id == instrument_id,
            Candle.timeframe == "5m",
            Candle.ts <= end_ts,
        )
    ).one()
    min_ts = _as_utc(min_ts)
    max_ts = _as_utc(max_ts)

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


def _build_backtest_plan(
    db: Session,
    *,
    requested_strategy: str,
    requested_start: datetime,
    requested_end: datetime,
    raw_params: dict[str, Any] | None,
    setting: Setting | None,
) -> dict[str, Any]:
    params = raw_params.copy() if isinstance(raw_params, dict) else {}
    runtime_strategy = _resolve_runtime_strategy_from_values(requested_strategy, params)
    strategy_profile = get_strategy_profile(runtime_strategy)
    profile_signal_params = strategy_profile.get("signal", {})
    profile_risk_params = strategy_profile.get("risk", {})
    profile_fee_params = strategy_profile.get("fees", {})
    profile_backtest_params = strategy_profile.get("backtest", {})

    if requested_strategy == STRATEGY_BREAKOUT_RETEST_2:
        params["history_min_coverage_ratio"] = BREAKOUT_RETEST_2_MIN_COVERAGE_RATIO
        params["history_target_coverage_ratio"] = BREAKOUT_RETEST_2_TARGET_COVERAGE_RATIO
        params["history_required_coverage_ratio"] = BREAKOUT_RETEST_2_TARGET_COVERAGE_RATIO
        current_tickers = _normalize_input_tickers(params.get("input_tickers"))
        merged_tickers = _normalize_input_tickers(
            BREAKOUT_RETEST_2_EXTRA_TICKERS + current_tickers + list(DEFAULT_UNIVERSE_INPUT)
        )
        params["input_tickers"] = merged_tickers
        params.setdefault("strategy_base_strategy", "StrategyBreakoutRetest")

    target_coverage_ratio = _to_float(
        params.get("history_target_coverage_ratio", params.get("target_coverage_ratio")),
        _to_float(
            profile_backtest_params.get("history_target_coverage_ratio"),
            DEFAULT_HISTORY_TARGET_COVERAGE_RATIO,
        ),
    )
    min_coverage_ratio = _to_float(
        params.get("history_min_coverage_ratio", params.get("min_coverage_ratio")),
        _to_float(
            profile_backtest_params.get("history_min_coverage_ratio"),
            DEFAULT_HISTORY_MIN_COVERAGE_RATIO,
        ),
    )
    required_coverage_ratio = _to_float(
        params.get("history_required_coverage_ratio"),
        _to_float(
            profile_backtest_params.get("history_required_coverage_ratio"),
            DEFAULT_HISTORY_REQUIRED_COVERAGE_RATIO,
        ),
    )

    target_coverage_ratio = min(max(target_coverage_ratio, 0.0), 1.0)
    min_coverage_ratio = min(max(min_coverage_ratio, 0.0), 1.0)
    required_coverage_ratio = min(max(required_coverage_ratio, 0.0), 1.0)
    if min_coverage_ratio > target_coverage_ratio:
        target_coverage_ratio = min_coverage_ratio
    if required_coverage_ratio < min_coverage_ratio:
        required_coverage_ratio = min_coverage_ratio

    input_tickers = _normalize_input_tickers(params.get("input_tickers"))
    if not input_tickers:
        input_tickers = _normalize_input_tickers(profile_backtest_params.get("input_tickers"))
    if not input_tickers:
        input_tickers = _normalize_input_tickers(setting.universe_json.get("input_tickers") if setting else None)
    if not input_tickers:
        input_tickers = _normalize_input_tickers(DEFAULT_UNIVERSE_INPUT)

    candidates, universe_source = _build_universe_candidates(
        db,
        input_tickers=input_tickers,
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
    effective_coverage_ratio = _effective_coverage_ratio(
        requested_start=requested_start,
        requested_end=requested_end,
        effective_start=effective_start,
    )
    data_availability_report = _build_data_availability_report(candidates)

    if not selected_symbols:
        readiness_reason = "no_symbols_with_min_coverage"
    elif effective_coverage_ratio < required_coverage_ratio:
        readiness_reason = "insufficient_common_history"
    else:
        readiness_reason = "ok"

    ready = readiness_reason == "ok"

    params["period_requested_start_ts"] = requested_start.isoformat()
    params["period_requested_end_ts"] = requested_end.isoformat()
    params["period_effective_start_ts"] = effective_start.isoformat()
    params["period_effective_end_ts"] = requested_end.isoformat()
    params["history_target_coverage_ratio"] = target_coverage_ratio
    params["history_min_coverage_ratio"] = min_coverage_ratio
    params["history_required_coverage_ratio"] = required_coverage_ratio
    params["history_effective_coverage_ratio"] = round(effective_coverage_ratio, 6)
    params["input_tickers"] = input_tickers
    params["strategy_requested"] = requested_strategy
    params["strategy_runtime"] = runtime_strategy

    return {
        "params": params,
        "requested_strategy": requested_strategy,
        "runtime_strategy": runtime_strategy,
        "strategy_profile": strategy_profile,
        "profile_signal_params": profile_signal_params,
        "profile_risk_params": profile_risk_params,
        "profile_fee_params": profile_fee_params,
        "profile_backtest_params": profile_backtest_params,
        "target_coverage_ratio": target_coverage_ratio,
        "min_coverage_ratio": min_coverage_ratio,
        "required_coverage_ratio": required_coverage_ratio,
        "effective_coverage_ratio": effective_coverage_ratio,
        "input_tickers": input_tickers,
        "candidates": candidates,
        "universe_source": universe_source,
        "selected": selected,
        "selected_symbols": selected_symbols,
        "effective_start": effective_start,
        "data_availability_report": data_availability_report,
        "ready": ready,
        "readiness_reason": readiness_reason,
    }


def inspect_backtest_history_readiness(
    db: Session,
    *,
    strategy: str,
    start_ts: datetime,
    end_ts: datetime,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    setting = db.scalar(select(Setting).order_by(Setting.id.asc()).limit(1))
    plan = _build_backtest_plan(
        db,
        requested_strategy=strategy,
        requested_start=start_ts,
        requested_end=end_ts,
        raw_params=params or {},
        setting=setting,
    )
    return {
        "ready": plan["ready"],
        "reason": plan["readiness_reason"],
        "strategy_requested": plan["requested_strategy"],
        "strategy_runtime": plan["runtime_strategy"],
        "period_requested": {"start_ts": start_ts.isoformat(), "end_ts": end_ts.isoformat()},
        "period_effective": {
            "start_ts": plan["effective_start"].isoformat(),
            "end_ts": end_ts.isoformat(),
        },
        "coverage": {
            "effective_ratio": round(plan["effective_coverage_ratio"], 6),
            "required_ratio": plan["required_coverage_ratio"],
            "min_ratio": plan["min_coverage_ratio"],
            "target_ratio": plan["target_coverage_ratio"],
        },
        "universe": {
            "input_tickers": plan["input_tickers"],
            "selected_top5": plan["selected_symbols"],
            "selection_source": plan["universe_source"],
        },
        "data_availability": plan["data_availability_report"],
    }


def _regime_filter_1h(
    candles_1h: list[CandleData],
    atr_threshold_pct: float,
) -> tuple[bool, dict]:
    if len(candles_1h) < 220:
        return False, {"reason": "insufficient_1h_history"}

    closes = [x.close for x in candles_1h]
    highs = [x.high for x in candles_1h]
    lows = [x.low for x in candles_1h]

    ema200 = ema(closes, 200)
    ema_now = ema200[-1]
    ema_prev = ema200[-5]
    slope = ema_now - ema_prev

    atr_1h = atr(highs, lows, closes, 14)[-1]
    atr_pct = (atr_1h / max(closes[-1], 1e-8)) * 100.0

    passed = closes[-1] > ema_now and slope >= 0 and atr_pct < atr_threshold_pct
    return passed, {
        "close_1h": closes[-1],
        "ema200_1h": ema_now,
        "ema200_slope": slope,
        "atr_pct_1h": atr_pct,
    }


def _simulate_raw_trades_for_symbol(
    symbol: str,
    candles_5m: list[CandleData],
    strategy: str,
    max_hold_hours: int,
    strategy_params: dict | None = None,
    candles_1h: list[CandleData] | None = None,
) -> list[RawTrade]:
    trades: list[RawTrade] = []
    i = 60
    params = strategy_params or {}
    hourly = candles_1h or []
    hourly_idx = -1

    while i < len(candles_5m) - 2:
        window = candles_5m[: i + 1]

        signal = None
        if strategy == "StrategyPullbackToTrend":
            signal = generate_pullback_signal(
                window,
                rsi_threshold=_to_float(params.get("pullback_rsi_threshold"), 45.0),
            )
        elif strategy == "MeanReversionHardStop":
            while hourly_idx + 1 < len(hourly) and hourly[hourly_idx + 1].ts <= window[-1].ts:
                hourly_idx += 1
            regime_window = hourly[: hourly_idx + 1] if hourly_idx >= 0 else []
            regime_ok, regime_meta = _regime_filter_1h(
                regime_window,
                atr_threshold_pct=_to_float(params.get("atr_threshold_pct_1h"), 4.0),
            )
            if regime_ok:
                signal = generate_mean_reversion_hard_stop_signal(
                    window,
                    bb_period=int(params.get("mr_bb_period", 20)),
                    bb_std=_to_float(params.get("mr_bb_std"), 2.0),
                    rsi_period=int(params.get("mr_rsi_period", 14)),
                    rsi_entry_threshold=_to_float(params.get("mr_rsi_entry_threshold"), 30.0),
                    safety_ema_period=int(params.get("mr_safety_ema_period", 200)),
                    lookback_stop=int(params.get("mr_lookback_stop", 15)),
                    stop_atr_buffer=_to_float(params.get("mr_stop_atr_buffer"), 0.2),
                    max_stop_pct=_to_float(params.get("mr_max_stop_pct"), 0.03),
                    tp_rr=_to_float(params.get("mr_tp_rr"), 1.2),
                    regime_meta=regime_meta,
                )
        elif strategy == "StrategyTrendRetrace70":
            signal = generate_trend_retrace_70_signal(
                window,
                ema_fast_period=int(params.get("tr70_ema_fast_period", 20)),
                ema_mid_period=int(params.get("tr70_ema_mid_period", 50)),
                ema_slow_period=int(params.get("tr70_ema_slow_period", 200)),
                pullback_lookback=int(params.get("tr70_pullback_lookback", 10)),
                pullback_depth_pct=_to_float(params.get("tr70_pullback_depth_pct"), 0.35),
                reclaim_buffer_pct=_to_float(params.get("tr70_reclaim_buffer_pct"), 0.05),
                rsi_period=int(params.get("tr70_rsi_period", 14)),
                rsi_min=_to_float(params.get("tr70_rsi_min"), 42.0),
                rsi_max=_to_float(params.get("tr70_rsi_max"), 62.0),
                stop_atr_mult=_to_float(params.get("tr70_stop_atr_mult"), 0.7),
                min_stop_pct=_to_float(params.get("tr70_min_stop_pct"), 0.7),
                max_stop_pct=_to_float(params.get("tr70_max_stop_pct"), 1.8),
                tp_rr=_to_float(params.get("tr70_tp_rr"), 2.1),
                min_volume_ratio=_to_float(params.get("tr70_min_volume_ratio"), 0.8),
            )
        else:
            signal = generate_breakout_retest_signal(
                window,
                lookback=int(params.get("breakout_lookback", 20)),
                retest_k_atr=_to_float(params.get("breakout_retest_k_atr"), 0.3),
                stop_atr_mult=_to_float(params.get("breakout_stop_atr_mult"), 1.0),
                tp_rr=_to_float(params.get("breakout_tp_rr"), 2.0),
                min_volume_ratio=_to_float(params.get("breakout_min_volume_ratio"), 0.0),
                min_confidence=_to_float(params.get("breakout_min_confidence"), 0.0),
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


def _simulate_raw_trades_for_symbols(
    selected_symbols: list[str],
    candles_5m_by_symbol: dict[str, list[CandleData]],
    strategy: str,
    max_hold_hours: int,
    strategy_params: dict,
    candles_1h_by_symbol: dict[str, list[CandleData]] | None = None,
) -> list[RawTrade]:
    raw_trades: list[RawTrade] = []
    hourly = candles_1h_by_symbol or {}

    for symbol in selected_symbols:
        candles_5m = candles_5m_by_symbol.get(symbol, [])
        if len(candles_5m) < 100:
            continue
        symbol_trades = _simulate_raw_trades_for_symbol(
            symbol=symbol,
            candles_5m=candles_5m,
            strategy=strategy,
            max_hold_hours=max_hold_hours,
            strategy_params=strategy_params,
            candles_1h=hourly.get(symbol, []),
        )
        raw_trades.extend(symbol_trades)

    raw_trades.sort(key=lambda x: x.exit_ts)
    return raw_trades


def _optimize_breakout_retest_2(
    selected_symbols: list[str],
    candles_5m_by_symbol: dict[str, list[CandleData]],
    max_hold_hours: int,
    strategy_params: dict,
    taker_fee_pct: float,
    entry_slippage_pct: float,
    exit_slippage_pct: float,
    stop_slippage_pct: float,
    initial_equity: float,
) -> tuple[list[RawTrade], dict]:
    lookback_values = [30, 40]
    retest_values = [0.1, 0.2, 0.3]
    stop_mult_values = [1.0, 1.3]
    tp_rr_values = [0.8, 1.0, 1.3]
    min_volume_values = [0.0, 1.0, 1.25]
    min_conf_values = [0.0, 0.55, 0.7, 0.85]
    symbol_top_n_values = [1, 3, 5]
    max_hold_values = sorted({max(1, int(max_hold_hours)), 6})

    candidate_count = 0
    best_raw: list[RawTrade] = []
    best_rank: tuple[float, float, float, float, float] | None = None
    best_details: dict[str, Any] = {}

    for (
        breakout_lookback,
        breakout_retest_k_atr,
        breakout_stop_atr_mult,
        breakout_tp_rr,
        breakout_min_volume_ratio,
        breakout_min_confidence,
        symbol_top_n,
        opt_max_hold_hours,
    ) in product(
        lookback_values,
        retest_values,
        stop_mult_values,
        tp_rr_values,
        min_volume_values,
        min_conf_values,
        symbol_top_n_values,
        max_hold_values,
    ):
        candidate_count += 1
        candidate_symbols = selected_symbols[: max(1, min(symbol_top_n, len(selected_symbols)))]
        candidate_params = strategy_params.copy()
        candidate_params.update(
            {
                "breakout_lookback": breakout_lookback,
                "breakout_retest_k_atr": breakout_retest_k_atr,
                "breakout_stop_atr_mult": breakout_stop_atr_mult,
                "breakout_tp_rr": breakout_tp_rr,
                "breakout_min_volume_ratio": breakout_min_volume_ratio,
                "breakout_min_confidence": breakout_min_confidence,
            }
        )

        raw_trades = _simulate_raw_trades_for_symbols(
            selected_symbols=candidate_symbols,
            candles_5m_by_symbol=candles_5m_by_symbol,
            strategy="StrategyBreakoutRetest",
            max_hold_hours=opt_max_hold_hours,
            strategy_params=candidate_params,
        )
        if not raw_trades:
            continue

        sim = _apply_execution_assumptions(
            raw_trades=raw_trades,
            taker_fee_pct=taker_fee_pct,
            entry_slippage_pct=entry_slippage_pct,
            exit_slippage_pct=exit_slippage_pct,
            stop_slippage_pct=stop_slippage_pct,
            multiplier=1.0,
        )
        metrics, _ = _build_metrics(sim, initial_equity)
        trades_count = int(metrics.get("trades", 0))
        if trades_count <= 0:
            continue

        winrate = float(metrics.get("winrate", 0.0))
        profit_factor = float(metrics.get("profit_factor", 0.0))
        meets_target = (
            profit_factor > BREAKOUT_RETEST_2_TARGET_PF
            and winrate >= BREAKOUT_RETEST_2_TARGET_WINRATE
        )
        meets_target_with_min_trades = meets_target and trades_count >= BREAKOUT_RETEST_2_MIN_TRADES_FOR_TARGET

        if meets_target_with_min_trades:
            rank = (3.0, profit_factor, winrate, float(trades_count), 0.0)
        elif meets_target:
            rank = (2.0, profit_factor, winrate, float(trades_count), 0.0)
        else:
            closeness_score = min(profit_factor / BREAKOUT_RETEST_2_TARGET_PF, 1.0) + min(
                winrate / BREAKOUT_RETEST_2_TARGET_WINRATE, 1.0
            )
            rank = (
                1.0,
                closeness_score,
                profit_factor,
                winrate,
                float(trades_count) / 1000.0,
            )

        if best_rank is None or rank > best_rank:
            best_rank = rank
            best_raw = raw_trades
            best_details = {
                "config": {
                    "breakout_lookback": breakout_lookback,
                    "breakout_retest_k_atr": breakout_retest_k_atr,
                    "breakout_stop_atr_mult": breakout_stop_atr_mult,
                    "breakout_tp_rr": breakout_tp_rr,
                    "breakout_min_volume_ratio": breakout_min_volume_ratio,
                    "breakout_min_confidence": breakout_min_confidence,
                    "breakout_symbol_top_n": len(candidate_symbols),
                    "breakout_max_hold_hours": opt_max_hold_hours,
                },
                "base_metrics": metrics,
                "meets_target": meets_target,
                "meets_target_with_min_trades": meets_target_with_min_trades,
            }

    if best_rank is None:
        fallback_raw = _simulate_raw_trades_for_symbols(
            selected_symbols=selected_symbols,
            candles_5m_by_symbol=candles_5m_by_symbol,
            strategy="StrategyBreakoutRetest",
            max_hold_hours=max_hold_hours,
            strategy_params=strategy_params,
        )
        fallback_sim = _apply_execution_assumptions(
            raw_trades=fallback_raw,
            taker_fee_pct=taker_fee_pct,
            entry_slippage_pct=entry_slippage_pct,
            exit_slippage_pct=exit_slippage_pct,
            stop_slippage_pct=stop_slippage_pct,
            multiplier=1.0,
        )
        fallback_metrics, _ = _build_metrics(fallback_sim, initial_equity)
        return fallback_raw, {
            "enabled": True,
            "target": {
                "profit_factor_gt": BREAKOUT_RETEST_2_TARGET_PF,
                "winrate_gte": BREAKOUT_RETEST_2_TARGET_WINRATE,
                "min_trades_for_target": BREAKOUT_RETEST_2_MIN_TRADES_FOR_TARGET,
            },
            "candidate_count": candidate_count,
            "target_met": False,
            "chosen_config": "default_strategy_params",
            "chosen_base_metrics": fallback_metrics,
        }

    return best_raw, {
        "enabled": True,
        "target": {
            "profit_factor_gt": BREAKOUT_RETEST_2_TARGET_PF,
            "winrate_gte": BREAKOUT_RETEST_2_TARGET_WINRATE,
            "min_trades_for_target": BREAKOUT_RETEST_2_MIN_TRADES_FOR_TARGET,
        },
        "candidate_count": candidate_count,
        "target_met": bool(best_details.get("meets_target_with_min_trades")),
        "target_met_relaxed": bool(best_details.get("meets_target")),
        "chosen_config": best_details.get("config"),
        "chosen_base_metrics": best_details.get("base_metrics"),
    }


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
        requested_strategy = backtest.strategy
        plan = _build_backtest_plan(
            db,
            requested_strategy=requested_strategy,
            requested_start=requested_start,
            requested_end=requested_end,
            raw_params=backtest.params_json,
            setting=setting,
        )
        params = plan["params"]
        runtime_strategy = plan["runtime_strategy"]
        strategy_profile = plan["strategy_profile"]
        profile_signal_params = plan["profile_signal_params"]
        profile_risk_params = plan["profile_risk_params"]
        profile_fee_params = plan["profile_fee_params"]
        target_coverage_ratio = plan["target_coverage_ratio"]
        min_coverage_ratio = plan["min_coverage_ratio"]
        required_coverage_ratio = plan["required_coverage_ratio"]
        effective_coverage_ratio = plan["effective_coverage_ratio"]
        input_tickers = plan["input_tickers"]
        universe_source = plan["universe_source"]
        selected_symbols = plan["selected_symbols"]
        effective_start = plan["effective_start"]
        data_availability_report = plan["data_availability_report"]

        backtest.universe_json = selected_symbols
        backtest.start_ts = effective_start
        backtest.params_json = params
        db.commit()

        if not plan["ready"]:
            backtest.status = "failed"
            backtest.metrics_json = {
                "error": (
                    "insufficient_history_coverage: "
                    f"effective={effective_coverage_ratio:.4f} "
                    f"required={required_coverage_ratio:.4f} "
                    f"reason={plan['readiness_reason']}"
                ),
                "base": {
                    "trades": 0,
                    "winrate": 0.0,
                    "profit_factor": 0.0,
                    "expectancy": 0.0,
                    "expectancy_r": 0.0,
                    "max_drawdown_pct": 0.0,
                    "avg_duration_min": 0.0,
                },
                "readiness": {
                    "ready": False,
                    "reason": plan["readiness_reason"],
                    "effective_coverage_ratio": round(effective_coverage_ratio, 6),
                    "required_coverage_ratio": required_coverage_ratio,
                    "selected_top5": selected_symbols,
                },
                "data_availability": data_availability_report,
            }
            backtest.equity_curve_json = []
            db.commit()
            db.refresh(backtest)
            return backtest

        max_hold_hours = int(
            backtest.params_json.get(
                "max_hold_hours",
                profile_risk_params.get("max_hold_hours", 72),
            )
        )
        strategy_params = profile_signal_params.copy()
        for key in list(strategy_params.keys()):
            if key in params:
                strategy_params[key] = params[key]
        optimization_meta: dict[str, Any] | None = None

        candles_5m_by_symbol: dict[str, list[CandleData]] = {}
        candles_1h_by_symbol: dict[str, list[CandleData]] = {}

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
            candles_5m_by_symbol[symbol] = candles_5m

            if runtime_strategy == "MeanReversionHardStop":
                candles_1h_by_symbol[symbol] = _load_candles(
                    db,
                    instrument.id,
                    "1h",
                    effective_start - timedelta(days=30),
                    requested_end,
                )

        if requested_strategy == STRATEGY_BREAKOUT_RETEST_2 and runtime_strategy == "StrategyBreakoutRetest":
            raw_trades, optimization_meta = _optimize_breakout_retest_2(
                selected_symbols=selected_symbols,
                candles_5m_by_symbol=candles_5m_by_symbol,
                max_hold_hours=max_hold_hours,
                strategy_params=strategy_params,
                taker_fee_pct=_to_float(profile_fee_params.get("taker_fee_pct"), DEFAULT_BACKTEST_TAKER_FEE_PCT),
                entry_slippage_pct=_to_float(profile_fee_params.get("backtest_entry_slippage_pct"), DEFAULT_BACKTEST_ENTRY_SLIPPAGE_PCT),
                exit_slippage_pct=_to_float(profile_fee_params.get("backtest_exit_slippage_pct"), DEFAULT_BACKTEST_EXIT_SLIPPAGE_PCT),
                stop_slippage_pct=_to_float(profile_fee_params.get("backtest_stop_slippage_pct"), DEFAULT_BACKTEST_STOP_SLIPPAGE_PCT),
                initial_equity=_to_float(backtest.params_json.get("initial_equity"), DEFAULT_BACKTEST_INITIAL_EQUITY),
            )
            chosen_config = optimization_meta.get("chosen_config") if optimization_meta else None
            if isinstance(chosen_config, dict):
                strategy_params.update(chosen_config)
        else:
            raw_trades = _simulate_raw_trades_for_symbols(
                selected_symbols=selected_symbols,
                candles_5m_by_symbol=candles_5m_by_symbol,
                strategy=runtime_strategy,
                max_hold_hours=max_hold_hours,
                strategy_params=strategy_params,
                candles_1h_by_symbol=candles_1h_by_symbol,
            )

        if requested_strategy == STRATEGY_BREAKOUT_RETEST_2:
            params["strategy_profile_params_applied"] = {
                key: strategy_params.get(key)
                for key in (
                    "breakout_lookback",
                    "breakout_retest_k_atr",
                    "breakout_stop_atr_mult",
                    "breakout_tp_rr",
                    "breakout_min_volume_ratio",
                    "breakout_min_confidence",
                    "breakout_symbol_top_n",
                    "breakout_max_hold_hours",
                )
                if key in strategy_params
            }
            backtest.params_json = params
            db.commit()

        taker_fee_pct = _to_float(
            params.get("taker_fee_pct"),
            _to_float(profile_fee_params.get("taker_fee_pct"), DEFAULT_BACKTEST_TAKER_FEE_PCT),
        )
        entry_slippage_pct = _to_float(
            params.get("backtest_entry_slippage_pct"),
            _to_float(
                profile_fee_params.get("backtest_entry_slippage_pct"),
                DEFAULT_BACKTEST_ENTRY_SLIPPAGE_PCT,
            ),
        )
        exit_slippage_pct = _to_float(
            params.get("backtest_exit_slippage_pct"),
            _to_float(
                profile_fee_params.get("backtest_exit_slippage_pct"),
                DEFAULT_BACKTEST_EXIT_SLIPPAGE_PCT,
            ),
        )
        stop_slippage_pct = _to_float(
            params.get("backtest_stop_slippage_pct"),
            _to_float(
                profile_fee_params.get("backtest_stop_slippage_pct"),
                DEFAULT_BACKTEST_STOP_SLIPPAGE_PCT,
            ),
        )
        initial_equity = _to_float(
            backtest.params_json.get("initial_equity"),
            _to_float(profile_risk_params.get("initial_equity"), DEFAULT_BACKTEST_INITIAL_EQUITY),
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
            "strategy_requested": requested_strategy,
            "strategy_runtime": runtime_strategy,
            "strategy_profile_source": "embedded_strategy_profile",
            "period_requested": {
                "start_ts": requested_start.isoformat(),
                "end_ts": requested_end.isoformat(),
            },
            "period_effective": {
                "start_ts": effective_start.isoformat(),
                "end_ts": requested_end.isoformat(),
            },
            "universe": {
                "input_tickers": input_tickers,
                "selected_top5": selected_symbols,
                "selection_source": universe_source,
                "selection_rules": [
                    "products: online + USDC quote",
                    "intersect with input tickers",
                    "rank by liquidity",
                    f"exclude coverage below {_format_ratio(min_coverage_ratio)}",
                    f"replace below target coverage {_format_ratio(target_coverage_ratio)}",
                ],
                "min_coverage_ratio": min_coverage_ratio,
                "target_coverage_ratio": target_coverage_ratio,
                "required_coverage_ratio": required_coverage_ratio,
                "effective_coverage_ratio": round(effective_coverage_ratio, 6),
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
        if optimization_meta:
            assumptions["strategy_optimization"] = optimization_meta

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
