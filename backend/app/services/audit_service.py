from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import LogEntry

logger = logging.getLogger(__name__)


def audit_log(db: Session | None, level: str, component: str, message: str, context: dict[str, Any] | None = None) -> None:
    payload = context or {}
    logger_method = getattr(logger, level.lower(), logger.info)
    logger_method(message, extra={"context": {"component": component, **payload}})
    if db is None:
        return
    entry = LogEntry(level=level.upper(), component=component, message=message, context_json=payload)
    db.add(entry)
    db.commit()
