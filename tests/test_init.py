"""Tests for setting up MedTrack and its entities."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, STATE_OFF, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.medtrack.const import DOMAIN

pytestmark = pytest.mark.usefixtures("notify_calls")

MEDICATION_ENTITIES = [
    "sensor.metformin_stock",
    "sensor.metformin_days_remaining",
    "sensor.metformin_next_dose",
    "sensor.metformin_status",
    "sensor.metformin_taken_today",
    "binary_sensor.metformin_low_stock",
    "button.metformin_take",
    "button.metformin_skip",
    "button.metformin_snooze",
    "button.metformin_refill",
    "button.metformin_undo_last",
    "sensor.metformin_last_action",
    "number.metformin_refill_amount",
]


async def test_setup_creates_devices_and_entities(
    hass: HomeAssistant, hub_entry: MockConfigEntry
) -> None:
    """Every medication gets a device with the spec's entities."""
    assert await hass.config_entries.async_setup(hub_entry.entry_id)
    await hass.async_block_till_done()
    assert hub_entry.state is ConfigEntryState.LOADED

    subentry_id = next(iter(hub_entry.subentries))
    dev_reg = dr.async_get(hass)
    hub_device = dev_reg.async_get_device_by_identifier(
        (DOMAIN, hub_entry.entry_id), hub_entry.entry_id
    )
    med_device = dev_reg.async_get_device_by_identifier(
        (DOMAIN, subentry_id), hub_entry.entry_id
    )
    assert hub_device is not None
    assert med_device is not None
    assert med_device.via_device_id == hub_device.id

    ent_reg = er.async_get(hass)
    for entity_id in MEDICATION_ENTITIES:
        entry = ent_reg.async_get(entity_id)
        assert entry is not None, entity_id
        assert entry.device_id == med_device.id
        assert entry.config_subentry_id == subentry_id
        assert entry.unique_id.startswith(subentry_id)

    for entity_id in (
        "sensor.medtrack_doses_due_today",
        "sensor.medtrack_doses_taken_today",
    ):
        entry = ent_reg.async_get(entity_id)
        assert entry is not None, entity_id
        assert entry.device_id == hub_device.id

    # Initial values.
    stock = hass.states.get("sensor.metformin_stock")
    assert stock.state == "30.0"
    assert stock.attributes[ATTR_UNIT_OF_MEASUREMENT] == "tablet"
    assert hass.states.get("sensor.metformin_status").state == "idle"
    assert hass.states.get("sensor.metformin_next_dose").state != STATE_UNKNOWN
    assert hass.states.get("binary_sensor.metformin_low_stock").state == STATE_OFF
    assert hass.states.get("sensor.metformin_taken_today").attributes["due_today"] == 2
    # 30 tablets, 2 tablets twice a day.
    assert hass.states.get("sensor.metformin_days_remaining").state == "7.5"
    refill = hass.states.get("number.metformin_refill_amount")
    assert refill.state == "60"
    assert refill.attributes["mode"] == "box"
    assert refill.attributes["step"] == 1
    assert hass.states.get("sensor.metformin_last_action").state == "None"
    assert refill.attributes[ATTR_UNIT_OF_MEASUREMENT] == "tablet"


async def test_unload(hass: HomeAssistant, hub_entry: MockConfigEntry) -> None:
    """The entry unloads cleanly."""
    assert await hass.config_entries.async_setup(hub_entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(hub_entry.entry_id)
    await hass.async_block_till_done()
    assert hub_entry.state is ConfigEntryState.NOT_LOADED
