"""Tests for the hub config flow, options flow and medication subentry flow."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    SOURCE_USER,
    ConfigEntryState,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.medtrack.const import (
    CONF_CRITICAL_VOLUME,
    CONF_DAYS,
    CONF_DOSE_AMOUNT,
    CONF_INSTRUCTIONS,
    CONF_MAX_REMINDERS,
    CONF_NOTIFY_TARGETS,
    CONF_REREMIND_INTERVAL,
    CONF_TIMES,
    DOMAIN,
    SECTION_OVERRIDES,
    SUBENTRY_MEDICATION,
)

from .conftest import HUB_OPTIONS, MEDICATION_DATA, TARGETS

# --- Hub flow ---------------------------------------------------------------


async def test_user_flow_aborts_without_phones(hass: HomeAssistant) -> None:
    """Without any Companion App notify service the flow cannot continue."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_notify_targets"


@pytest.mark.usefixtures("notify_calls")
async def test_user_flow_creates_hub(hass: HomeAssistant) -> None:
    """The user step creates an entry with settings in options."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "  Family meds ", CONF_NOTIFY_TARGETS: [TARGETS[0]]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Family meds"
    assert result["data"] == {}
    options = result["options"]
    assert options[CONF_NOTIFY_TARGETS] == [TARGETS[0]]
    # Untouched fields get the spec defaults.
    assert options[CONF_REREMIND_INTERVAL] == 10
    assert options[CONF_MAX_REMINDERS] == 4
    assert options[CONF_CRITICAL_VOLUME] == 1.0
    assert isinstance(options[CONF_REREMIND_INTERVAL], int)


@pytest.mark.usefixtures("notify_calls")
async def test_user_flow_requires_a_phone(hass: HomeAssistant) -> None:
    """Submitting no phones shows an error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "MedTrack", CONF_NOTIFY_TARGETS: []}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_NOTIFY_TARGETS: "no_targets"}


@pytest.mark.usefixtures("notify_calls")
async def test_user_flow_rejects_out_of_range(hass: HomeAssistant) -> None:
    """Numeric ranges from the spec are enforced."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with pytest.raises(InvalidData, match="reremind_interval"):
        await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_NOTIFY_TARGETS: TARGETS, CONF_REREMIND_INTERVAL: 500},
        )


@pytest.mark.usefixtures("notify_calls")
async def test_options_flow_updates_and_reloads(
    hass: HomeAssistant, hub_entry: MockConfigEntry
) -> None:
    """Changing options stores them and reloads the entry."""
    assert await hass.config_entries.async_setup(hub_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(hub_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    new_options = HUB_OPTIONS | {
        CONF_NOTIFY_TARGETS: [TARGETS[1]],
        CONF_MAX_REMINDERS: 6,
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], new_options
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert hub_entry.options[CONF_MAX_REMINDERS] == 6
    assert hub_entry.options[CONF_NOTIFY_TARGETS] == [TARGETS[1]]
    assert hub_entry.state is ConfigEntryState.LOADED  # loaded again after reload


async def test_options_flow_keeps_missing_phone_selectable(
    hass: HomeAssistant, hub_entry: MockConfigEntry
) -> None:
    """A configured phone that is currently missing can still be saved."""
    # No notify services registered at all.
    result = await hass.config_entries.options.async_init(hub_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], HUB_OPTIONS
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


# --- Medication subentry flow -----------------------------------------------


def _medication_input(**overrides: Any) -> dict[str, Any]:
    """Form input for a valid medication."""
    values: dict[str, Any] = {
        CONF_NAME: "Vitamin D",
        **MEDICATION_DATA,
        CONF_TIMES: ["20:00:00", "8:00"],
    }
    values.update(overrides)
    return values


async def _start_add(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, Any]:
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_MEDICATION), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


@pytest.mark.usefixtures("notify_calls")
async def test_add_medication(hass: HomeAssistant, hub_entry: MockConfigEntry) -> None:
    """Adding a medication stores normalised data and creates its device."""
    assert await hass.config_entries.async_setup(hub_entry.entry_id)
    await hass.async_block_till_done()

    result = await _start_add(hass, hub_entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        _medication_input(
            **{CONF_INSTRUCTIONS: "  ", SECTION_OVERRIDES: {CONF_REREMIND_INTERVAL: 5}}
        ),
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY

    subentry = next(s for s in hub_entry.subentries.values() if s.title == "Vitamin D")
    assert subentry.subentry_type == SUBENTRY_MEDICATION
    assert subentry.data[CONF_TIMES] == ["08:00", "20:00"]
    assert subentry.data[CONF_REREMIND_INTERVAL] == 5
    assert CONF_MAX_REMINDERS not in subentry.data
    assert CONF_INSTRUCTIONS not in subentry.data  # blank text is dropped
    assert SECTION_OVERRIDES not in subentry.data

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, subentry.subentry_id), hub_entry.entry_id
    )
    assert device is not None
    assert device.name == "Vitamin D"
    assert device.config_entry_id == hub_entry.entry_id
    assert device.config_subentry_id == subentry.subentry_id
    assert hass.states.get("sensor.vitamin_d_stock") is not None


@pytest.mark.parametrize(
    ("overrides", "field", "error"),
    [
        ({CONF_NAME: "metformin "}, CONF_NAME, "name_exists"),
        ({CONF_NAME: "  "}, CONF_NAME, "name_required"),
        ({CONF_DOSE_AMOUNT: 0}, CONF_DOSE_AMOUNT, "invalid_dose"),
        ({CONF_TIMES: []}, CONF_TIMES, "no_times"),
        ({CONF_TIMES: ["08:00", "8:00"]}, CONF_TIMES, "duplicate_time"),
        ({CONF_TIMES: ["25:00"]}, CONF_TIMES, "invalid_time"),
        ({CONF_TIMES: ["08:00:30"]}, CONF_TIMES, "invalid_time"),
        ({CONF_DAYS: []}, CONF_DAYS, "no_days"),
    ],
)
async def test_add_medication_validation(
    hass: HomeAssistant,
    hub_entry: MockConfigEntry,
    overrides: dict[str, Any],
    field: str,
    error: str,
) -> None:
    """Invalid input shows the matching error on the field."""
    result = await _start_add(hass, hub_entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], _medication_input(**overrides)
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {field: error}


@pytest.mark.usefixtures("notify_calls")
async def test_reconfigure_medication(
    hass: HomeAssistant, hub_entry: MockConfigEntry
) -> None:
    """Reconfigure pre-fills the form, saves changes and reloads."""
    assert await hass.config_entries.async_setup(hub_entry.entry_id)
    await hass.async_block_till_done()
    subentry_id = next(iter(hub_entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (hub_entry.entry_id, SUBENTRY_MEDICATION),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry_id},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    # The stored values are offered as suggestions.
    suggested = {
        str(key): key.description["suggested_value"]
        for key in result["data_schema"].schema
        if key.description and "suggested_value" in key.description
    }
    assert suggested[CONF_NAME] == "Metformin"
    assert suggested[CONF_TIMES] == ["08:00", "20:00"]

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        _medication_input(
            **{
                CONF_NAME: "Metformin XR",
                CONF_TIMES: ["07:30"],
                SECTION_OVERRIDES: {CONF_MAX_REMINDERS: 2},
            }
        ),
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    subentry = hub_entry.subentries[subentry_id]
    assert subentry.title == "Metformin XR"
    assert subentry.data[CONF_TIMES] == ["07:30"]
    assert subentry.data[CONF_MAX_REMINDERS] == 2
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, subentry_id), hub_entry.entry_id
    )
    assert device is not None
    assert device.name == "Metformin XR"  # reload picked up the new name


@pytest.mark.usefixtures("notify_calls")
async def test_reconfigure_keeps_own_name(
    hass: HomeAssistant, hub_entry: MockConfigEntry
) -> None:
    """Saving a medication under its own name is not a duplicate."""
    subentry_id = next(iter(hub_entry.subentries))
    result = await hass.config_entries.subentries.async_init(
        (hub_entry.entry_id, SUBENTRY_MEDICATION),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry_id},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], _medication_input(**{CONF_NAME: "METFORMIN"})
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"


@pytest.mark.usefixtures("notify_calls")
async def test_remove_medication(
    hass: HomeAssistant, hub_entry: MockConfigEntry
) -> None:
    """Removing a medication removes its device and entities."""
    assert await hass.config_entries.async_setup(hub_entry.entry_id)
    await hass.async_block_till_done()
    subentry_id = next(iter(hub_entry.subentries))
    ent_reg = er.async_get(hass)
    assert ent_reg.async_get("sensor.metformin_stock") is not None

    assert hass.config_entries.async_remove_subentry(hub_entry, subentry_id)
    await hass.async_block_till_done()

    assert subentry_id not in hub_entry.subentries
    assert (
        dr.async_get(hass).async_get_device_by_identifier(
            (DOMAIN, subentry_id), hub_entry.entry_id
        )
        is None
    )
    assert ent_reg.async_get("sensor.metformin_stock") is None
    assert hass.states.get("sensor.metformin_stock") is None
