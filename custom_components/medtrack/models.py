"""Plain data models for MedTrack (no Home Assistant objects inside)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .const import (
    CONF_ACTIVE,
    CONF_CRITICAL_MODE,
    CONF_CURRENT_STOCK,
    CONF_DAYS,
    CONF_DOSE_AMOUNT,
    CONF_INSTRUCTIONS,
    CONF_LOW_STOCK_THRESHOLD,
    CONF_MAX_REMINDERS,
    CONF_REFILL_AMOUNT,
    CONF_REREMIND_INTERVAL,
    CONF_TIMES,
    CONF_UNIT,
    CriticalMode,
)


@dataclass(frozen=True, kw_only=True)
class Medication:
    """Configuration of one medication (built from its config subentry)."""

    subentry_id: str
    name: str
    dose_amount: float
    unit: str
    times: tuple[str, ...]
    days: tuple[str, ...]
    instructions: str | None
    initial_stock: float
    low_stock_threshold: float
    refill_amount: float | None
    critical_mode: CriticalMode
    active: bool
    reremind_interval: int | None
    max_reminders: int | None

    @classmethod
    def from_subentry(
        cls, subentry_id: str, title: str, data: Mapping[str, Any]
    ) -> Medication:
        """Build a Medication from stored subentry data."""
        return cls(
            subentry_id=subentry_id,
            name=title,
            dose_amount=float(data[CONF_DOSE_AMOUNT]),
            unit=str(data[CONF_UNIT]),
            times=tuple(data[CONF_TIMES]),
            days=tuple(data[CONF_DAYS]),
            instructions=data.get(CONF_INSTRUCTIONS),
            initial_stock=float(data.get(CONF_CURRENT_STOCK, 0)),
            low_stock_threshold=float(data.get(CONF_LOW_STOCK_THRESHOLD, 0)),
            refill_amount=(
                float(data[CONF_REFILL_AMOUNT])
                if data.get(CONF_REFILL_AMOUNT) is not None
                else None
            ),
            critical_mode=CriticalMode(
                data.get(CONF_CRITICAL_MODE, CriticalMode.NEVER)
            ),
            active=bool(data.get(CONF_ACTIVE, True)),
            reremind_interval=data.get(CONF_REREMIND_INTERVAL),
            max_reminders=data.get(CONF_MAX_REMINDERS),
        )
