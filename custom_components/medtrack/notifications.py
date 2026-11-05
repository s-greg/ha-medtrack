"""Notification payloads (spec §6) and sending them to every phone."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import (
    ACTION_PREFIX,
    DOMAIN,
    NOTIFY_GROUP,
    NOTIFY_TIMEOUT,
    TAG_PREFIX,
    ActionVerb,
    CriticalMode,
)
from .models import Dose, HubSettings, Medication
from .targets import target_label

_LOGGER = logging.getLogger(__name__)

CLEAR_MESSAGE = "clear_notification"


# --- Pure payload builders ------------------------------------------------------


def dose_tag(dose_id: str) -> str:
    """Notification tag of a dose (same tag = replace instead of stack)."""
    return f"{TAG_PREFIX}{dose_id}"


def action_id(verb: ActionVerb, dose_id: str) -> str:
    """Action identifier, unique per dose: MEDTRACK_<VERB>_<dose_id>."""
    return f"{ACTION_PREFIX}{verb}_{dose_id}"


def parse_action(action: str) -> tuple[ActionVerb, str] | None:
    """Split MEDTRACK_<VERB>_<id>; None for actions that are not ours."""
    if not action.startswith(ACTION_PREFIX):
        return None
    verb, _, ident = action.removeprefix(ACTION_PREFIX).partition("_")
    try:
        return ActionVerb(verb), ident
    except ValueError:
        return None


def format_amount(amount: float) -> str:
    """2.0 -> '2', 0.5 -> '0.5'."""
    return f"{amount:g}"


def _body(medication: Medication, dose: Dose) -> str:
    """'<dose amount> <unit> · <HH:MM>< · instructions>'."""
    body = (
        f"{format_amount(medication.dose_amount)} {medication.unit} · {dose.local_time}"
    )
    if medication.instructions:
        body += f" · {medication.instructions}"
    return body


def _push(level: str, settings: HubSettings, critical: bool) -> dict[str, Any]:
    """The `push` block; critical sends also get the critical sound."""
    if not critical:
        return {"interruption-level": level}
    return {
        "interruption-level": "critical",
        "sound": {"name": "default", "critical": 1, "volume": settings.critical_volume},
    }


def _taken_action(dose_id: str, title: str, settings: HubSettings) -> dict[str, Any]:
    action: dict[str, Any] = {
        "action": action_id(ActionVerb.TAKEN, dose_id),
        "title": title,
    }
    if settings.require_unlock:
        action["authenticationRequired"] = True
    return action


def reminder_payload(
    medication: Medication,
    dose: Dose,
    settings: HubSettings,
    *,
    number: int,
    max_reminders: int,
) -> dict[str, Any]:
    """Service data for reminder `number` of `max_reminders`."""
    critical = medication.critical_mode == CriticalMode.ALL_REMINDERS or (
        medication.critical_mode == CriticalMode.FINAL_REMINDER
        and number >= max_reminders
    )
    actions = [_taken_action(dose.dose_id, "Taken", settings)]
    if dose.snoozes_used < settings.max_snoozes:
        actions.append(
            {
                "action": action_id(ActionVerb.SNOOZE, dose.dose_id),
                "title": f"Snooze {settings.snooze_length} min",
            }
        )
    actions.append(
        {
            "action": action_id(ActionVerb.SKIP, dose.dose_id),
            "title": "Skip",
            "destructive": True,
        }
    )
    return {
        "title": f"Medication: {medication.name}",
        "message": f"{_body(medication, dose)} (reminder {number} of {max_reminders})",
        "data": {
            "tag": dose_tag(dose.dose_id),
            "group": NOTIFY_GROUP,
            "push": _push(settings.interruption_level, settings, critical),
            "action_data": {"dose_id": dose.dose_id},
            "actions": actions,
        },
    }


def missed_payload(
    medication: Medication, dose: Dose, settings: HubSettings
) -> dict[str, Any]:
    """Service data for the final 'Missed' notification (only 'Taken late')."""
    return {
        "title": f"Missed: {medication.name}",
        "message": _body(medication, dose),
        "data": {
            "tag": dose_tag(dose.dose_id),
            "group": NOTIFY_GROUP,
            "push": _push(settings.interruption_level, settings, critical=False),
            "action_data": {"dose_id": dose.dose_id},
            "actions": [_taken_action(dose.dose_id, "Taken late", settings)],
        },
    }


def clear_payload(tag: str) -> dict[str, Any]:
    """Service data that removes a notification from the phone."""
    return {"message": CLEAR_MESSAGE, "data": {"tag": tag}}


# --- Sending ----------------------------------------------------------------------


def _issue_id(entry_id: str, target: str) -> str:
    return f"missing_target_{entry_id}_{target}"


class Notifier:
    """Sends payloads to all configured phones concurrently.

    A failing phone is logged and skipped; it never raises into the scheduler.
    Phones are referred to by position in logs (their names are personal);
    a missing phone also raises a Repairs issue with its friendly name.
    """

    def __init__(
        self, hass: HomeAssistant, entry_id: str, targets: Iterable[str]
    ) -> None:
        """Store the targets (notify service names)."""
        self._hass = hass
        self._entry_id = entry_id
        self._targets = tuple(targets)

    async def async_send(self, payload: dict[str, Any]) -> None:
        """Send one payload to every target."""
        await asyncio.gather(
            *(
                self._async_send_one(index, target, payload)
                for index, target in enumerate(self._targets, start=1)
            )
        )

    async def async_clear(self, tag: str) -> None:
        """Remove a notification from every target."""
        await self.async_send(clear_payload(tag))

    async def _async_send_one(
        self, index: int, target: str, payload: dict[str, Any]
    ) -> None:
        if not self._hass.services.has_service("notify", target):
            _LOGGER.warning(
                "Notify target %d of %d is not available (phone renamed or "
                "removed?); see Settings > Repairs",
                index,
                len(self._targets),
            )
            ir.async_create_issue(
                self._hass,
                DOMAIN,
                _issue_id(self._entry_id, target),
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="missing_target",
                translation_placeholders={"target": target_label(self._hass, target)},
            )
            return
        try:
            async with asyncio.timeout(NOTIFY_TIMEOUT):
                await self._hass.services.async_call(
                    "notify", target, payload, blocking=True
                )
        except Exception as err:
            _LOGGER.warning(
                "Sending to notify target %d of %d failed: %s",
                index,
                len(self._targets),
                type(err).__name__,
            )
            return
        ir.async_delete_issue(self._hass, DOMAIN, _issue_id(self._entry_id, target))
