"""Number entities for MedTrack (the amount the Refill button adds)."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MedTrackConfigEntry, MedTrackManager
from .entity import MedicationEntity
from .models import display_precision

PARALLEL_UPDATES = 0

REFILL_AMOUNT = NumberEntityDescription(
    key="refill_amount",
    translation_key="refill_amount",
    mode=NumberMode.BOX,
    native_min_value=0,
    native_max_value=100_000,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MedTrackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create "Refill amount" for every medication that tracks stock."""
    manager = entry.runtime_data

    @callback
    def add_medication(subentry_id: str) -> None:
        if manager.medications[subentry_id].tracks_stock:
            async_add_entities(
                [RefillAmountNumber(manager, subentry_id, REFILL_AMOUNT)],
                config_subentry_id=subentry_id,
            )

    manager.async_register_entity_adder(add_medication)
    for subentry_id in manager.medications:
        add_medication(subentry_id)


class RefillAmountNumber(MedicationEntity, NumberEntity):
    """How much the Refill button adds; resets to the default after a refill.

    Empty (unknown) when the medication has no default refill amount.
    """

    def __init__(
        self,
        manager: MedTrackManager,
        subentry_id: str,
        description: NumberEntityDescription,
    ) -> None:
        """Use the medication's unit; decimals only if its dose has them."""
        super().__init__(manager, subentry_id, description)
        medication = manager.medications[subentry_id]
        self._attr_native_unit_of_measurement = medication.unit
        self._attr_native_step = 10 ** -display_precision(medication.dose_amount)

    @property
    def native_value(self) -> float | None:
        """The amount the next refill adds."""
        return self._manager.refill_input.get(self._subentry_id)

    async def async_set_native_value(self, value: float) -> None:
        """Remember the typed amount for the Refill button."""
        self._manager.async_set_refill_input(self._subentry_id, value)
