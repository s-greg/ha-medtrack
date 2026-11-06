"""Diagnostics for MedTrack (Settings > Devices & services > MedTrack > ...).

Contains the configuration, stock, running doses and the last 50 history
records. Phone (notify target) names and user IDs are redacted; medication
details are kept because they are needed to understand a problem.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_NOTIFY_TARGETS, DIAGNOSTICS_HISTORY, DoseState
from .coordinator import MedTrackConfigEntry

TO_REDACT = {CONF_NOTIFY_TARGETS, "user_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MedTrackConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a hub."""
    manager = entry.runtime_data
    return async_redact_data(
        {
            "entry": {
                "title": entry.title,
                "options": dict(entry.options),
                "notify_target_count": len(manager.settings.notify_targets),
            },
            "medications": {
                sid: {
                    "title": subentry.title,
                    "data": dict(subentry.data),
                    "stock": asdict(manager.stock_state[sid])
                    if sid in manager.stock_state
                    else None,
                    "days_remaining": manager.days_remaining(sid)
                    if sid in manager.medications
                    else None,
                }
                for sid, subentry in entry.subentries.items()
            },
            "doses": [
                dose.as_dict()
                for dose in sorted(
                    manager.doses.values(), key=lambda dose: dose.scheduled_at
                )
                if dose.state != DoseState.SCHEDULED
            ],
            "next_scheduled": {
                sid: next_dose.isoformat()
                for sid in manager.medications
                if (next_dose := manager.next_dose(sid)) is not None
            },
            "history_count": len(manager.history),
            "history": manager.history[-DIAGNOSTICS_HISTORY:],
        },
        TO_REDACT,
    )
