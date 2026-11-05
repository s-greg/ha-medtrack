"""Persistent storage for MedTrack (doses in flight, stock, history)."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_MINOR_VERSION, STORAGE_VERSION

# Stored layout, schema version 1:
# {
#   "last_seen": ISO UTC time of the last write (used for restart recovery),
#   "doses": [Dose.as_dict(), ...]   (everything except not-yet-due doses),
#   "medications": {subentry_id: {"stock", "config_stock", "low_stock_alerted"}},
#   "history": [record, ...]          (spec §10)
# }
type StoreData = dict[str, Any]


class MedTrackStore(Store[StoreData]):
    """Store with a migration hook for future schema versions."""

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: StoreData
    ) -> StoreData:
        """Migrate stored data to the current schema.

        Only version 1 exists so far; add steps here when the layout changes.
        """
        return old_data


def create_store(hass: HomeAssistant, entry_id: str) -> MedTrackStore:
    """Create the store for one hub: .storage/medtrack.<entry_id>."""
    return MedTrackStore(
        hass,
        STORAGE_VERSION,
        f"{DOMAIN}.{entry_id}",
        minor_version=STORAGE_MINOR_VERSION,
    )
