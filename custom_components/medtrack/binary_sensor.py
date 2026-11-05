"""Binary sensors for MedTrack."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MedTrackConfigEntry
from .entity import MedicationEntity

PARALLEL_UPDATES = 0

LOW_STOCK = BinarySensorEntityDescription(
    key="low_stock",
    translation_key="low_stock",
    device_class=BinarySensorDeviceClass.PROBLEM,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MedTrackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the low-stock sensor for every medication that tracks stock."""
    manager = entry.runtime_data

    @callback
    def add_medication(subentry_id: str) -> None:
        if manager.medications[subentry_id].tracks_stock:
            async_add_entities(
                [LowStockSensor(manager, subentry_id, LOW_STOCK)],
                config_subentry_id=subentry_id,
            )

    manager.async_register_entity_adder(add_medication)
    for subentry_id in manager.medications:
        add_medication(subentry_id)


class LowStockSensor(MedicationEntity, BinarySensorEntity):
    """On when stock is at or below the medication's threshold."""

    @property
    def is_on(self) -> bool:
        """Return True when stock is low."""
        return self._manager.low_stock(self._subentry_id)
