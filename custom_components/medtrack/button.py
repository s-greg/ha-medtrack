"""Buttons for MedTrack (take / skip / snooze the current dose, refill)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import Source
from .coordinator import MedTrackConfigEntry, MedTrackManager
from .entity import MedicationEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class MedTrackButtonDescription(ButtonEntityDescription):
    """Describes a MedTrack button."""

    press_fn: Callable[[MedTrackManager, str, str | None], Awaitable[object]]
    # Only created when stock tracking is on for the medication.
    requires_stock: bool = False


BUTTONS: tuple[MedTrackButtonDescription, ...] = (
    MedTrackButtonDescription(
        key="take",
        translation_key="take",
        press_fn=lambda m, sid, uid: m.async_take(sid, user_id=uid),
    ),
    MedTrackButtonDescription(
        key="skip",
        translation_key="skip",
        press_fn=lambda m, sid, uid: m.async_skip(sid, user_id=uid),
    ),
    MedTrackButtonDescription(
        key="snooze",
        translation_key="snooze",
        press_fn=lambda m, sid, uid: m.async_snooze(sid, user_id=uid),
    ),
    MedTrackButtonDescription(
        key="undo_last",
        translation_key="undo_last",
        press_fn=lambda m, sid, uid: m.async_undo_last(
            sid, user_id=uid, source=Source.BUTTON
        ),
    ),
    MedTrackButtonDescription(
        key="refill",
        translation_key="refill",
        press_fn=lambda m, sid, uid: m.async_refill(
            sid,
            m.refill_input.get(sid),
            user_id=uid,
            source=Source.BUTTON,
            reset_input=True,
        ),
        requires_stock=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MedTrackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the buttons for every medication."""
    manager = entry.runtime_data

    @callback
    def add_medication(subentry_id: str) -> None:
        medication = manager.medications[subentry_id]
        async_add_entities(
            [
                MedTrackButton(manager, subentry_id, description)
                for description in BUTTONS
                if medication.tracks_stock or not description.requires_stock
            ],
            config_subentry_id=subentry_id,
        )

    manager.async_register_entity_adder(add_medication)
    for subentry_id in manager.medications:
        add_medication(subentry_id)


class MedTrackButton(MedicationEntity, ButtonEntity):
    """A dose action button."""

    entity_description: MedTrackButtonDescription

    async def async_press(self) -> None:
        """Run the action for this medication."""
        await self.entity_description.press_fn(
            self._manager,
            self._subentry_id,
            self._context.user_id if self._context else None,
        )
