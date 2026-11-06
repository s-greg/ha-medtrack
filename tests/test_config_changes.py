"""Owner's Phase 2 test round: config changes without reload, and the fixes."""

from __future__ import annotations

import asyncio
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER, ConfigSubentry
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, STATE_UNAVAILABLE
from homeassistant.core import Event, HomeAssistant, ServiceCall
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.medtrack.const import (
    CONF_ACTIVE,
    CONF_CURRENT_STOCK,
    CONF_DOSE_AMOUNT,
    CONF_REFILL_AMOUNT,
    CONF_REREMIND_INTERVAL,
    CONF_TAP_PATH,
    CONF_TIMES,
    CONF_UNIT,
    SUBENTRY_MEDICATION,
)
from custom_components.medtrack.notifications import format_quantity

from .conftest import (
    HUB_OPTIONS,
    MEDICATION_DATA,
    TARGETS,
    clears,
    device_page,
    local,
    make_hub_entry,
    move_to,
    reminders,
    reset,
    tap,
    use_amsterdam,
)

Calls = dict[str, list[ServiceCall]]


async def _start(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    medication: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    start=None,
) -> tuple[MockConfigEntry, str]:
    await use_amsterdam(hass)
    freezer.move_to(start or local(2026, 11, 5, 7, 0))
    entry = make_hub_entry(hass, medication, options)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, next(iter(entry.subentries))


def _edit(hass: HomeAssistant, entry: MockConfigEntry, sid: str, **changes) -> None:
    subentry = entry.subentries[sid]
    hass.config_entries.async_update_subentry(
        entry, subentry, data={**subentry.data, **changes}
    )


def _went_unavailable(events: list[Event]) -> list[str]:
    return [
        e.data["entity_id"]
        for e in events
        if e.data["new_state"] is not None
        and e.data["new_state"].state == STATE_UNAVAILABLE
    ]


# --- Bug 1: delete a medication and add one with the same name ----------------------


async def test_readded_medication_shows_its_own_stock(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Nothing of a deleted medication leaks into a new one with the same name."""
    entry, old = await _start(hass, freezer)
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await tap(hass, f"MEDTRACK_TAKEN_{old}_20261105_0800", f"{old}_20261105_0800")
    assert hass.states.get("sensor.metformin_stock").state == "28.0"

    assert hass.config_entries.async_remove_subentry(entry, old)
    await hass.async_block_till_done()
    manager = entry.runtime_data
    assert old not in manager.stock_state
    assert old not in manager._data()["medications"]
    assert not any(d.subentry_id == old for d in manager.doses.values())

    new_sub = ConfigSubentry(
        data=MEDICATION_DATA | {CONF_CURRENT_STOCK: 11.0, CONF_DOSE_AMOUNT: 1.0},
        subentry_type=SUBENTRY_MEDICATION,
        title="Metformin",
        unique_id=None,
    )
    hass.config_entries.async_add_subentry(entry, new_sub)
    await hass.async_block_till_done()
    new = new_sub.subentry_id

    ent_reg = er.async_get(hass)
    stock_id = ent_reg.async_get_entity_id("sensor", "medtrack", f"{new}_stock")
    assert stock_id == "sensor.metformin_stock"  # no leftover _2 entity
    assert ent_reg.async_get_entity_id("sensor", "medtrack", f"{old}_stock") is None
    assert hass.states.get(stock_id).state == "11.0"

    for when, hhmm in (
        (local(2026, 11, 5, 20, 0), "2000"),
        (local(2026, 11, 6, 8, 0), "0800"),
    ):
        await move_to(hass, freezer, when)
        day = when.strftime("%Y%m%d")
        await tap(hass, f"MEDTRACK_TAKEN_{new}_{day}_{hhmm}", f"{new}_{day}_{hhmm}")
        assert hass.states.get(stock_id).state == str(manager.stock(new))
    assert manager.stock(new) == 9.0

    # Also after a full reload (as after a restart).
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(stock_id).state == "9.0"


# --- Bug 2: early take and saving the medication form --------------------------------


async def test_early_take_survives_form_save_and_reload(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """The owner's timeline: 21:07 taken, 21:09 taken early, form saved at 21:08."""
    entry, sid = await _start(
        hass,
        freezer,
        {
            CONF_TIMES: ["21:07", "21:09"],
            CONF_CURRENT_STOCK: 11.0,
            CONF_DOSE_AMOUNT: 1.0,
        },
        {CONF_REREMIND_INTERVAL: 1},
        start=local(2026, 11, 5, 21, 0),
    )
    await move_to(hass, freezer, local(2026, 11, 5, 21, 7))
    await move_to(hass, freezer, local(2026, 11, 5, 21, 7, 12))
    await tap(hass, f"MEDTRACK_TAKEN_{sid}_20261105_2107", f"{sid}_20261105_2107")
    await move_to(hass, freezer, local(2026, 11, 5, 21, 7, 42))
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.metformin_take"}, blocking=True
    )
    assert hass.states.get("sensor.metformin_stock").state == "9.0"

    await move_to(hass, freezer, local(2026, 11, 5, 21, 8, 17))
    _edit(hass, entry, sid, instructions="after dinner")
    await hass.async_block_till_done()
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    reset(notify_calls)

    await move_to(hass, freezer, local(2026, 11, 5, 21, 9))
    await move_to(hass, freezer, local(2026, 11, 5, 21, 10))
    assert reminders(notify_calls) == []
    assert hass.states.get("sensor.metformin_status").state == "taken"
    assert hass.states.get("sensor.metformin_stock").state == "9.0"


async def test_unload_during_send_leaves_no_running_copy(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """An unload while a reminder is being sent must not leave a timer behind.

    Before the fix the old copy re-armed its timer after sending, kept sending
    reminders and could write its outdated data over the new copy's.
    """
    release = asyncio.Event()
    calls: list[ServiceCall] = []

    async def slow_phone(call: ServiceCall) -> None:
        calls.append(call)
        await release.wait()

    for target in TARGETS:
        hass.services.async_register("notify", target, slow_phone)

    entry, _ = await _start(hass, freezer)
    freezer.move_to(local(2026, 11, 5, 8, 0))
    async_fire_time_changed(hass, local(2026, 11, 5, 8, 0))
    for _ in range(20):
        await asyncio.sleep(0)
    assert len(calls) == len(TARGETS)  # reminder 1 is being sent

    manager = entry.runtime_data
    unload = hass.async_create_task(hass.config_entries.async_unload(entry.entry_id))
    for _ in range(20):
        await asyncio.sleep(0)
    release.set()
    assert await unload
    await hass.async_block_till_done()

    assert manager._unsub_timer is None
    calls.clear()
    await move_to(hass, freezer, local(2026, 11, 5, 8, 10))
    await move_to(hass, freezer, local(2026, 11, 5, 8, 20))
    assert calls == []


# --- Bug 3: default refill amount is optional ----------------------------------------


@pytest.mark.parametrize("empty", ["omitted", None, "", 0, 0.0])
async def test_refill_amount_may_be_empty(
    hass: HomeAssistant, hub_entry: MockConfigEntry, notify_calls: Calls, empty
) -> None:
    """An empty refill (left out, null, "" or the 0 a cleared box sends) is
    accepted and not stored: no default refill amount."""
    assert await hass.config_entries.async_setup(hub_entry.entry_id)
    await hass.async_block_till_done()
    user_input: dict[str, Any] = {
        "name": "Vitamin D",
        CONF_DOSE_AMOUNT: 1,
        CONF_UNIT: "capsule",
        CONF_TIMES: ["09:00"],
        "days": ["mon"],
        "critical_mode": "never",
        CONF_ACTIVE: True,
        "low_stock_threshold": "" if empty == "" else None,
        "overrides": {CONF_REREMIND_INTERVAL: None},
    }
    if empty != "omitted":
        user_input[CONF_REFILL_AMOUNT] = empty
    flow = await hass.config_entries.subentries.async_init(
        (hub_entry.entry_id, SUBENTRY_MEDICATION), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        flow["flow_id"], user_input
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    subentry = next(s for s in hub_entry.subentries.values() if s.title == "Vitamin D")
    assert CONF_REFILL_AMOUNT not in subentry.data
    assert "low_stock_threshold" not in subentry.data
    assert CONF_REREMIND_INTERVAL not in subentry.data


async def test_clearing_refill_in_edit_form_removes_default(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Editing: a cleared refill box (0) removes the default; a threshold of 0
    is kept as a real threshold."""
    entry, sid = await _start(hass, freezer)
    flow = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_MEDICATION),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": sid},
    )
    data = {k: v for k, v in entry.subentries[sid].data.items()}
    result = await hass.config_entries.subentries.async_configure(
        flow["flow_id"],
        {
            "name": "Metformin",
            **data,
            CONF_REFILL_AMOUNT: 0,
            "low_stock_threshold": 0,
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert CONF_REFILL_AMOUNT not in entry.subentries[sid].data
    assert entry.subentries[sid].data["low_stock_threshold"] == 0.0
    assert hass.states.get("number.metformin_refill_amount").state == "unknown"


# --- Improvement 4: tapping a reminder opens a page -----------------------------------


async def test_reminders_open_device_page_or_custom_path(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Default: the medication's device page. The hub option overrides it."""
    entry, sid = await _start(hass, freezer)
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    assert reminders(notify_calls)[-1]["data"]["url"] == device_page(hass, sid)

    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_TAP_PATH: "/lovelace/medication"}
    )
    await hass.async_block_till_done()
    await move_to(hass, freezer, local(2026, 11, 5, 8, 10))
    assert reminders(notify_calls)[-1]["data"]["url"] == "/lovelace/medication"


async def test_tap_path_option_is_validated(
    hass: HomeAssistant, hub_entry: MockConfigEntry, notify_calls: Calls
) -> None:
    """A path must start with / (or be a full URL); empty means device page."""
    assert await hass.config_entries.async_setup(hub_entry.entry_id)
    await hass.async_block_till_done()
    flow = await hass.config_entries.options.async_init(hub_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {**HUB_OPTIONS, CONF_TAP_PATH: "lovelace"}
    )
    assert result["errors"] == {CONF_TAP_PATH: "invalid_tap_path"}
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {**HUB_OPTIONS, CONF_TAP_PATH: "  "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_TAP_PATH not in hub_entry.options


# --- Improvement 5: no reload, no "unavailable" flood -------------------------------


async def test_saving_forms_does_not_reload(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Options and medication edits are applied in place."""
    entry, sid = await _start(hass, freezer)
    manager = entry.runtime_data
    events = async_capture_events(hass, "state_changed")

    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_REREMIND_INTERVAL: 5}
    )
    await hass.async_block_till_done()
    _edit(
        hass, entry, sid, instructions="after breakfast", **{CONF_CURRENT_STOCK: 40.0}
    )
    await hass.async_block_till_done()
    hass.config_entries.async_update_subentry(
        entry, entry.subentries[sid], title="Metformin XR"
    )
    await hass.async_block_till_done()

    assert entry.runtime_data is manager
    assert _went_unavailable(events) == []
    assert manager.settings.reremind_interval == 5
    assert hass.states.get("sensor.metformin_stock").state == "40.0"
    assert (
        hass.states.get("sensor.metformin_stock").attributes["friendly_name"]
        == "Metformin XR Stock"
    )

    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    assert reminders(notify_calls)[-1]["message"] == (
        "2 tablets · 08:00 · after breakfast (reminder 1 of 4)"
    )
    assert reminders(notify_calls)[-1]["title"] == "Medication: Metformin XR"
    await move_to(hass, freezer, local(2026, 11, 5, 8, 5))  # new 5 min interval
    assert len(reminders(notify_calls)) == 2


async def test_changing_times_rebuilds_schedule(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """New times take effect without a reload; removed times no longer fire."""
    entry, sid = await _start(hass, freezer)
    _edit(hass, entry, sid, **{CONF_TIMES: ["07:30"]})
    await hass.async_block_till_done()
    assert (
        hass.states.get("sensor.metformin_next_dose").state
        == "2026-11-05T06:30:00+00:00"
    )
    await move_to(hass, freezer, local(2026, 11, 5, 7, 30))
    assert len(reminders(notify_calls)) == 1
    await tap(hass, f"MEDTRACK_TAKEN_{sid}_20261105_0730", f"{sid}_20261105_0730")
    reset(notify_calls)
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    assert reminders(notify_calls) == []


async def test_unit_change_recreates_only_that_medication(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """A new unit re-creates this medication's entities with the same IDs."""
    entry, sid = await _start(hass, freezer)
    _edit(hass, entry, sid, **{CONF_UNIT: "capsule"})
    await hass.async_block_till_done()
    stock = hass.states.get("sensor.metformin_stock")
    assert stock.attributes[ATTR_UNIT_OF_MEASUREMENT] == "capsule"
    assert stock.state == "30.0"
    assert hass.states.get("button.metformin_take") is not None
    assert len(entry.runtime_data._entities[sid]) == 13


# --- Improvement 6: pausing records the running dose ----------------------------------


async def test_pausing_records_running_dose_as_skipped(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Pausing while a dose runs: skipped (source system), notification cleared."""
    entry, sid = await _start(hass, freezer)
    dose_id = f"{sid}_20261105_0800"
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    skipped = async_capture_events(hass, "medtrack_dose_skipped")
    reset(notify_calls)

    _edit(hass, entry, sid, **{CONF_ACTIVE: False})
    await hass.async_block_till_done()

    assert len(skipped) == 1
    record = entry.runtime_data.history[-1]
    assert record["dose_id"] == dose_id
    assert record["state"] == "skipped"
    assert record["source"] == "system"
    assert record["reason"] == "paused"
    assert clears(notify_calls) == [f"medtrack_{dose_id}"]
    assert hass.states.get("sensor.metformin_status").state == "skipped"

    await move_to(hass, freezer, local(2026, 11, 5, 8, 10))
    await move_to(hass, freezer, local(2026, 11, 5, 20, 0))
    assert reminders(notify_calls) == []
    assert hass.states.get("sensor.metformin_next_dose").state == "unknown"


# --- Improvement 7: plural units ---------------------------------------------------


@pytest.mark.parametrize(
    ("amount", "unit", "text"),
    [
        (2, "tablet", "2 tablets"),
        (1, "tablet", "1 tablet"),
        (0.5, "tablet", "0.5 tablet"),
        (1.5, "capsule", "1.5 capsules"),
        (2, "patch", "2 patches"),
        (2, "eye drop", "2 eye drops"),
        (3, "puffs", "3 puffs"),
        (5, "ml", "5 ml"),
        (400, "IU", "400 IU"),
        (2, "spray", "2 sprays"),
    ],
)
def test_format_quantity(amount: float, unit: str, text: str) -> None:
    """Simple English plurals; abbreviations stay as typed."""
    assert format_quantity(amount, unit) == text
