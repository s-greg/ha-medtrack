"""The MedTrack manager: one per hub config entry, stored on entry.runtime_data.

Phase 1: holds the medication configuration and serves placeholder values to
the entities. Scheduling, notifications, inventory and history are added in
later phases.
"""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN, STATUS_IDLE, SUBENTRY_MEDICATION
from .models import Medication

_LOGGER = logging.getLogger(__name__)

type MedTrackConfigEntry = ConfigEntry[MedTrackManager]


def signal_update(entry_id: str) -> str:
    """Dispatcher signal fired when any MedTrack state of this entry changes."""
    return f"{DOMAIN}_{entry_id}_update"


class MedTrackManager:
    """Owns everything MedTrack does for one hub."""

    def __init__(self, hass: HomeAssistant, entry: MedTrackConfigEntry) -> None:
        """Initialise from the config entry and its medication subentries."""
        self.hass = hass
        self.entry = entry
        self.medications: dict[str, Medication] = {
            subentry_id: Medication.from_subentry(
                subentry_id, subentry.title, subentry.data
            )
            for subentry_id, subentry in entry.subentries.items()
            if subentry.subentry_type == SUBENTRY_MEDICATION
        }

    async def async_setup(self) -> None:
        """Start the manager (timers and listeners come in Phase 2)."""

    async def async_shutdown(self) -> None:
        """Stop timers and listeners."""

    @callback
    def async_notify_entities(self) -> None:
        """Tell entities to refresh their state."""
        async_dispatcher_send(self.hass, signal_update(self.entry.entry_id))

    # --- Values read by entities (placeholders in Phase 1) ---------------------

    def stock(self, subentry_id: str) -> float:
        """Current stock in the medication's unit."""
        return self.medications[subentry_id].initial_stock

    def days_remaining(self, subentry_id: str) -> float | None:
        """Days until stock runs out at the scheduled use."""
        return None

    def next_dose(self, subentry_id: str) -> datetime | None:
        """Time of the next scheduled dose."""
        return None

    def status(self, subentry_id: str) -> str:
        """State of the most recent dose."""
        return STATUS_IDLE

    def status_attributes(self, subentry_id: str) -> dict[str, Any]:
        """Details of the current dose."""
        return {"dose_id": None, "reminders_sent": 0, "snoozes_used": 0}

    def taken_today(self, subentry_id: str) -> int:
        """Doses of this medication taken today."""
        return 0

    def due_today(self, subentry_id: str) -> int:
        """Doses of this medication scheduled today."""
        return 0

    def low_stock(self, subentry_id: str) -> bool:
        """Whether stock is at or below the threshold."""
        med = self.medications[subentry_id]
        return self.stock(subentry_id) <= med.low_stock_threshold

    def hub_doses_due_today(self) -> int:
        """Doses of all medications scheduled today."""
        return sum(self.due_today(sid) for sid in self.medications)

    def hub_doses_taken_today(self) -> int:
        """Doses of all medications taken today."""
        return sum(self.taken_today(sid) for sid in self.medications)

    # --- Actions (implemented in Phase 2) -------------------------------------

    async def async_take(self, subentry_id: str, *, user_id: str | None) -> None:
        """Confirm the pending (or early) dose of a medication."""
        _LOGGER.info(
            "Take requested for %s; dose handling not implemented yet",
            self.medications[subentry_id].name,
        )

    async def async_skip(self, subentry_id: str, *, user_id: str | None) -> None:
        """Skip the pending dose of a medication."""
        _LOGGER.info(
            "Skip requested for %s; dose handling not implemented yet",
            self.medications[subentry_id].name,
        )

    async def async_snooze(self, subentry_id: str, *, user_id: str | None) -> None:
        """Snooze the pending dose of a medication."""
        _LOGGER.info(
            "Snooze requested for %s; dose handling not implemented yet",
            self.medications[subentry_id].name,
        )
