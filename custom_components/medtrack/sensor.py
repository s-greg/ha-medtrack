"""Sensors for MedTrack."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import STATUS_OPTIONS
from .coordinator import MedTrackConfigEntry, MedTrackManager
from .entity import HubEntity, MedicationEntity
from .models import display_precision

PARALLEL_UPDATES = 0

type Value = float | int | str | datetime | None


@dataclass(frozen=True, kw_only=True)
class MedicationSensorDescription(SensorEntityDescription):
    """Describes a per-medication sensor."""

    value_fn: Callable[[MedTrackManager, str], Value]
    attrs_fn: Callable[[MedTrackManager, str], dict[str, Any]] | None = None
    # Only created when stock tracking is on for the medication.
    requires_stock: bool = False


@dataclass(frozen=True, kw_only=True)
class HubSensorDescription(SensorEntityDescription):
    """Describes a hub sensor."""

    value_fn: Callable[[MedTrackManager], Value]


MEDICATION_SENSORS: tuple[MedicationSensorDescription, ...] = (
    MedicationSensorDescription(
        key="stock",
        translation_key="stock",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda m, sid: m.stock(sid),
        requires_stock=True,
    ),
    MedicationSensorDescription(
        key="days_remaining",
        translation_key="days_remaining",
        native_unit_of_measurement=UnitOfTime.DAYS,
        suggested_display_precision=0,
        value_fn=lambda m, sid: m.days_remaining(sid),
        requires_stock=True,
    ),
    MedicationSensorDescription(
        key="next_dose",
        translation_key="next_dose",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda m, sid: m.next_dose(sid),
    ),
    MedicationSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=[str(option) for option in STATUS_OPTIONS],
        value_fn=lambda m, sid: m.status(sid),
        attrs_fn=lambda m, sid: m.status_attributes(sid),
    ),
    MedicationSensorDescription(
        key="taken_today",
        translation_key="taken_today",
        value_fn=lambda m, sid: m.taken_today(sid),
        attrs_fn=lambda m, sid: {"due_today": m.due_today(sid)},
    ),
)

HUB_SENSORS: tuple[HubSensorDescription, ...] = (
    HubSensorDescription(
        key="doses_due_today",
        translation_key="doses_due_today",
        value_fn=lambda m: m.hub_doses_due_today(),
    ),
    HubSensorDescription(
        key="doses_taken_today",
        translation_key="doses_taken_today",
        value_fn=lambda m: m.hub_doses_taken_today(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MedTrackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the sensors for every medication and for the hub."""
    manager = entry.runtime_data

    @callback
    def add_medication(subentry_id: str) -> None:
        medication = manager.medications[subentry_id]
        async_add_entities(
            [
                MedicationSensor(manager, subentry_id, description)
                for description in MEDICATION_SENSORS
                if medication.tracks_stock or not description.requires_stock
            ],
            config_subentry_id=subentry_id,
        )

    manager.async_register_entity_adder(add_medication)
    for subentry_id in manager.medications:
        add_medication(subentry_id)
    async_add_entities(HubSensor(manager, description) for description in HUB_SENSORS)


class MedicationSensor(MedicationEntity, SensorEntity):
    """A sensor on a medication device."""

    entity_description: MedicationSensorDescription

    def __init__(
        self,
        manager: MedTrackManager,
        subentry_id: str,
        description: MedicationSensorDescription,
    ) -> None:
        """Initialise; the stock sensor uses the medication's own unit.

        Stock is shown as a whole number unless the dose itself has decimals.
        """
        super().__init__(manager, subentry_id, description)
        if description.key == "stock":
            medication = manager.medications[subentry_id]
            self._attr_native_unit_of_measurement = medication.unit
            self._attr_suggested_display_precision = display_precision(
                medication.dose_amount
            )

    @property
    def native_value(self) -> Value:
        """Return the current value."""
        return self.entity_description.value_fn(self._manager, self._subentry_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes, if any."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self._manager, self._subentry_id)


class HubSensor(HubEntity, SensorEntity):
    """A sensor on the hub device."""

    entity_description: HubSensorDescription

    @property
    def native_value(self) -> Value:
        """Return the current value."""
        return self.entity_description.value_fn(self._manager)
