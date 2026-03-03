from datetime import datetime, timedelta, timezone

from app.execution.paper import _consecutive_losses
from app.models.entities import Trade


def test_consecutive_stop_losses_count_only_today(db_session):
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)

    db_session.add_all(
        [
            Trade(
                mode="paper",
                instrument_id=1,
                side="buy",
                qty_base=1.0,
                qty_quote=100.0,
                entry_price=100.0,
                exit_price=98.0,
                fees=0.0,
                pnl=-2.0,
                opened_at=yesterday - timedelta(minutes=10),
                closed_at=yesterday,
                status="closed",
                order_ids_json={},
                meta_json={"exit_reason": "stop"},
            ),
            Trade(
                mode="paper",
                instrument_id=1,
                side="buy",
                qty_base=1.0,
                qty_quote=100.0,
                entry_price=100.0,
                exit_price=99.0,
                fees=0.0,
                pnl=-1.0,
                opened_at=now - timedelta(minutes=20),
                closed_at=now - timedelta(minutes=15),
                status="closed",
                order_ids_json={},
                meta_json={"exit_reason": "stop"},
            ),
            Trade(
                mode="paper",
                instrument_id=1,
                side="buy",
                qty_base=1.0,
                qty_quote=100.0,
                entry_price=100.0,
                exit_price=99.5,
                fees=0.0,
                pnl=-0.5,
                opened_at=now - timedelta(minutes=10),
                closed_at=now - timedelta(minutes=5),
                status="closed",
                order_ids_json={},
                meta_json={"exit_reason": "stop"},
            ),
        ]
    )
    db_session.commit()

    assert _consecutive_losses(db_session, "paper") == 2


def test_consecutive_losses_break_on_non_stop_exit(db_session):
    now = datetime.now(timezone.utc)

    db_session.add_all(
        [
            Trade(
                mode="paper",
                instrument_id=1,
                side="buy",
                qty_base=1.0,
                qty_quote=100.0,
                entry_price=100.0,
                exit_price=99.0,
                fees=0.0,
                pnl=-1.0,
                opened_at=now - timedelta(minutes=15),
                closed_at=now - timedelta(minutes=10),
                status="closed",
                order_ids_json={},
                meta_json={"exit_reason": "stop"},
            ),
            Trade(
                mode="paper",
                instrument_id=1,
                side="buy",
                qty_base=1.0,
                qty_quote=100.0,
                entry_price=100.0,
                exit_price=99.0,
                fees=0.0,
                pnl=-1.0,
                opened_at=now - timedelta(minutes=8),
                closed_at=now - timedelta(minutes=6),
                status="closed",
                order_ids_json={},
                meta_json={"exit_reason": "timeout"},
            ),
        ]
    )
    db_session.commit()

    assert _consecutive_losses(db_session, "paper") == 0
