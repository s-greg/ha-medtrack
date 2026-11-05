"""Tests for phone names in the hub flows and optional stock tracking."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.const import CONF_NAME, STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.medtrack.const import (
    CONF_CURRENT_STOCK,
    CONF_LOW_STOCK_THRESHOLD,
    CONF_NOTIFY_TARGETS,
    DOMAIN,
    SUBENTRY_MEDICATION,
)
from custom_components.medtrack.models import display_precision

from .conftest import MEDICATION_DATA

pytestmark = pytest.mark.usefixtures("notify_calls")


def _selector_options(result: dict[str, Any], key: str) -> list[dict[str, str]]:
    for schema_key, validator in result["data_schema"].schema.items():
        if str(schema_key) == key:
            return validator.config["options"]
    raise KeyError(key)


async def test_phone_list_shows_friendly_names(hass: HomeAssistant) -> None:
    """Phones are labelled with their Companion App name, stored by service."""
    MockConfigEntry(
        domain="mobile_app", data={"device_name": "Alex's iPad"}
    ).add_to_hass(hass)
    MockConfigEntry(domain="mobile_app", data={"device_name": "Phone A"}).add_to_hass(
        hass
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    options = _selector_options(result, CONF_NOTIFY_TARGETS)
    # phone_b has no mobile_app entry, so it falls back to the service name.
    assert options == [
        {"value": "mobile_app_phone_b", "label": "mobile_app_phone_b"},
        {"value": "mobile_app_phone_a", "label": "Phone A"},
    ]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "MedTrack", CONF_NOTIFY_TARGETS: ["mobile_app_phone_a"]},
    )
    assert result["options"][CONF_NOTIFY_TARGETS] == ["mobile_app_phone_a"]


async def test_options_flow_shows_friendly_names(
    hass: HomeAssistant, hub_entry: MockConfigEntry
) -> None:
    """The options flow uses the same labels."""
    MockConfigEntry(domain="mobile_app", data={"device_name": "Phone B"}).add_to_hass(
        hass
    )
    result = await hass.config_entries.options.async_init(hub_entry.entry_id)
    labels = {
        o["value"]: o["label"] for o in _selector_options(result, CONF_NOTIFY_TARGETS)
    }
    assert labels["mobile_app_phone_b"] == "Phone B"


def _entry_with_medication(
    hass: HomeAssistant, data: dict[str, Any]
) -> MockConfigEntry:
    from homeassistant.config_entries import ConfigSubentryData

    from .conftest import HUB_OPTIONS

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="MedTrack",
        data={},
        options=HUB_OPTIONS,
        subentries_data=[
            ConfigSubentryData(
                data=data,
                subentry_type=SUBENTRY_MEDICATION,
                title="Metformin",
                unique_id=None,
            )
        ],
    )
    entry.add_to_hass(hass)
    return entry


async def test_no_stock_means_no_stock_entities(hass: HomeAssistant) -> None:
    """Without a stock value the stock-related entities are not created."""
    data = {
        k: v
        for k, v in MEDICATION_DATA.items()
        if k not in (CONF_CURRENT_STOCK, CONF_LOW_STOCK_THRESHOLD)
    }
    entry = _entry_with_medication(hass, data)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    assert ent_reg.async_get("sensor.metformin_stock") is None
    assert ent_reg.async_get("sensor.metformin_days_remaining") is None
    assert ent_reg.async_get("binary_sensor.metformin_low_stock") is None
    assert ent_reg.async_get("sensor.metformin_status") is not None
    assert ent_reg.async_get("button.metformin_take") is not None


async def test_stock_without_threshold_never_low(hass: HomeAssistant) -> None:
    """Stock without a threshold is tracked but never reported low."""
    data = MEDICATION_DATA | {CONF_CURRENT_STOCK: 0.0}
    data.pop(CONF_LOW_STOCK_THRESHOLD)
    entry = _entry_with_medication(hass, data)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.metformin_stock").state == "0.0"
    assert hass.states.get("binary_sensor.metformin_low_stock").state == STATE_OFF


async def test_toggle_stock_tracking_via_reconfigure(
    hass: HomeAssistant, hub_entry: MockConfigEntry
) -> None:
    """Clearing stock removes the entities; filling it in brings them back."""
    assert await hass.config_entries.async_setup(hub_entry.entry_id)
    await hass.async_block_till_done()
    ent_reg = er.async_get(hass)
    subentry_id = next(iter(hub_entry.subentries))

    async def reconfigure(stock: float | None) -> None:
        result = await hass.config_entries.subentries.async_init(
            (hub_entry.entry_id, SUBENTRY_MEDICATION),
            context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry_id},
        )
        user_input = {CONF_NAME: "Metformin", **MEDICATION_DATA}
        user_input.pop(CONF_CURRENT_STOCK)
        if stock is not None:
            user_input[CONF_CURRENT_STOCK] = stock
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], user_input
        )
        assert result["reason"] == "reconfigure_successful"
        await hass.async_block_till_done()

    await reconfigure(None)
    assert CONF_CURRENT_STOCK not in hub_entry.subentries[subentry_id].data
    assert ent_reg.async_get("sensor.metformin_stock") is None
    assert ent_reg.async_get("binary_sensor.metformin_low_stock") is None
    assert hass.states.get("sensor.metformin_stock") is None

    await reconfigure(12.0)
    assert ent_reg.async_get("sensor.metformin_stock") is not None
    assert hass.states.get("sensor.metformin_stock").state == "12.0"


@pytest.mark.parametrize(
    ("amount", "precision"), [(2.0, 0), (1.0, 0), (0.5, 1), (1.25, 2), (0.1234, 3)]
)
def test_display_precision(amount: float, precision: int) -> None:
    """Stock decimals follow the dose amount."""
    assert display_precision(amount) == precision


async def test_stock_sensor_precision(
    hass: HomeAssistant, hub_entry: MockConfigEntry
) -> None:
    """A whole-number dose gives a whole-number stock display."""
    assert await hass.config_entries.async_setup(hub_entry.entry_id)
    await hass.async_block_till_done()
    entry = er.async_get(hass).async_get("sensor.metformin_stock")
    assert entry.options["sensor"]["suggested_display_precision"] == 0
