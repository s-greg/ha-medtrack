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


def _optional_float(value: Any) -> float | None:
    """Float, or None when the value is missing."""
    return None if value is None else float(value)


def display_precision(amount: float) -> int:
    """Decimals needed to show an amount: 2.0 -> 0, 0.5 -> 1, 0.25 -> 2 (max 3)."""
    for decimals in range(4):
        if round(amount, decimals) == amount:
            return decimals
    return 3


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
    initial_stock: float | None  # None = stock tracking off
    low_stock_threshold: float | None  # None = never alert
    refill_amount: float | None
    critical_mode: CriticalMode
    active: bool
    reremind_interval: int | None
    max_reminders: int | None

    @property
    def tracks_stock(self) -> bool:
        """Whether stock is tracked for this medication."""
        return self.initial_stock is not None

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
            initial_stock=_optional_float(data.get(CONF_CURRENT_STOCK)),
            low_stock_threshold=_optional_float(data.get(CONF_LOW_STOCK_THRESHOLD)),
            refill_amount=_optional_float(data.get(CONF_REFILL_AMOUNT)),
            critical_mode=CriticalMode(
                data.get(CONF_CRITICAL_MODE, CriticalMode.NEVER)
            ),
            active=bool(data.get(CONF_ACTIVE, True)),
            reremind_interval=data.get(CONF_REREMIND_INTERVAL),
            max_reminders=data.get(CONF_MAX_REMINDERS),
        )
