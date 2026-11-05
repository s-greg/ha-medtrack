"""Plain data models for MedTrack (no Home Assistant objects inside)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .const import (
    ACTIVE_STATES,
    CONF_ACTIVE,
    CONF_CRITICAL_MODE,
    CONF_CRITICAL_VOLUME,
    CONF_CURRENT_STOCK,
    CONF_DAYS,
    CONF_DOSE_AMOUNT,
    CONF_EARLY_TAKE_WINDOW,
    CONF_HISTORY_RETENTION,
    CONF_INSTRUCTIONS,
    CONF_INTERRUPTION_LEVEL,
    CONF_LOW_STOCK_THRESHOLD,
    CONF_MAX_REMINDERS,
    CONF_MAX_SNOOZES,
    CONF_NOTIFY_TARGETS,
    CONF_REFILL_AMOUNT,
    CONF_REQUIRE_UNLOCK,
    CONF_REREMIND_INTERVAL,
    CONF_SNOOZE_LENGTH,
    CONF_TAP_PATH,
    CONF_TIMES,
    CONF_UNIT,
    DEFAULT_CRITICAL_VOLUME,
    DEFAULT_EARLY_TAKE_WINDOW,
    DEFAULT_HISTORY_RETENTION,
    DEFAULT_INTERRUPTION_LEVEL,
    DEFAULT_MAX_REMINDERS,
    DEFAULT_MAX_SNOOZES,
    DEFAULT_REQUIRE_UNLOCK,
    DEFAULT_REREMIND_INTERVAL,
    DEFAULT_SNOOZE_LENGTH,
    CriticalMode,
    DoseState,
)


@dataclass(frozen=True, kw_only=True)
class HubSettings:
    """Global settings of a hub (from entry.options)."""

    notify_targets: tuple[str, ...]
    reremind_interval: int
    max_reminders: int
    max_snoozes: int
    snooze_length: int
    early_take_window: int
    interruption_level: str
    require_unlock: bool
    critical_volume: float
    history_retention: int
    tap_path: str | None = None  # None = the medication's device page

    @classmethod
    def from_options(cls, options: Mapping[str, Any]) -> HubSettings:
        """Build from entry.options, falling back to the defaults."""
        return cls(
            notify_targets=tuple(options.get(CONF_NOTIFY_TARGETS, ())),
            reremind_interval=int(
                options.get(CONF_REREMIND_INTERVAL, DEFAULT_REREMIND_INTERVAL)
            ),
            max_reminders=int(options.get(CONF_MAX_REMINDERS, DEFAULT_MAX_REMINDERS)),
            max_snoozes=int(options.get(CONF_MAX_SNOOZES, DEFAULT_MAX_SNOOZES)),
            snooze_length=int(options.get(CONF_SNOOZE_LENGTH, DEFAULT_SNOOZE_LENGTH)),
            early_take_window=int(
                options.get(CONF_EARLY_TAKE_WINDOW, DEFAULT_EARLY_TAKE_WINDOW)
            ),
            interruption_level=str(
                options.get(CONF_INTERRUPTION_LEVEL, DEFAULT_INTERRUPTION_LEVEL)
            ),
            require_unlock=bool(
                options.get(CONF_REQUIRE_UNLOCK, DEFAULT_REQUIRE_UNLOCK)
            ),
            critical_volume=float(
                options.get(CONF_CRITICAL_VOLUME, DEFAULT_CRITICAL_VOLUME)
            ),
            history_retention=int(
                options.get(CONF_HISTORY_RETENTION, DEFAULT_HISTORY_RETENTION)
            ),
            tap_path=str(options.get(CONF_TAP_PATH) or "").strip() or None,
        )


@dataclass(kw_only=True)
class Dose:
    """Runtime state of one dose (mutable; persisted unless still scheduled)."""

    dose_id: str
    subentry_id: str
    local_time: str  # nominal "HH:MM", shown in notifications
    scheduled_at: datetime  # UTC
    state: DoseState = DoseState.SCHEDULED
    reminders_sent: int = 0
    snoozes_used: int = 0
    # When the timer next has to do something with this dose (UTC):
    # first reminder, next reminder, missed, or dropping it from memory.
    next_action_at: datetime | None = None
    resolved_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        """Pending or snoozed: reminders are running."""
        return self.state in ACTIVE_STATES

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the Store (UTC ISO 8601 timestamps)."""
        return {
            "dose_id": self.dose_id,
            "subentry_id": self.subentry_id,
            "local_time": self.local_time,
            "scheduled_at": self.scheduled_at.isoformat(),
            "state": self.state.value,
            "reminders_sent": self.reminders_sent,
            "snoozes_used": self.snoozes_used,
            "next_action_at": _iso(self.next_action_at),
            "resolved_at": _iso(self.resolved_at),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Dose:
        """Restore from the Store."""
        return cls(
            dose_id=data["dose_id"],
            subentry_id=data["subentry_id"],
            local_time=data["local_time"],
            scheduled_at=datetime.fromisoformat(data["scheduled_at"]),
            state=DoseState(data["state"]),
            reminders_sent=int(data.get("reminders_sent", 0)),
            snoozes_used=int(data.get("snoozes_used", 0)),
            next_action_at=_parse(data.get("next_action_at")),
            resolved_at=_parse(data.get("resolved_at")),
        )


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _parse(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


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
