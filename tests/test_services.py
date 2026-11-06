"""MedTrack actions (spec §9), undo_last (spec §12 test 10) and get_history."""

from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, selector
from homeassistant.util.yaml import load_yaml_dict
import probatio
import pytest

from custom_components.medtrack.const import (
    CONF_CURRENT_STOCK,
    CONF_REFILL_AMOUNT,
    DOMAIN,
    SUBENTRY_MEDICATION,
)

from .conftest import (
    MEDICATION_DATA,
    clears,
    history,
    local,
    medication_device_id,
    medtrack,
    move_to,
    press,
    reminders,
    reset,
    start_hub,
)

Calls = dict[str, list[ServiceCall]]
STOCK = "sensor.metformin_stock"


def _stock(hass: HomeAssistant) -> float:
    return float(hass.states.get(STOCK).state)


def _status(hass: HomeAssistant) -> str:
    return hass.states.get("sensor.metformin_status").state


async def _error(hass: HomeAssistant, service: str, data: dict) -> str:
    with pytest.raises(ServiceValidationError) as err:
        await medtrack(hass, service, data)
    return str(err.value.translation_key)


# --- take / skip / snooze ------------------------------------------------------------


async def test_take_dose_by_device(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """take_dose on the medication's device confirms the running dose."""
    entry, sid = await start_hub(hass, freezer)
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await medtrack(hass, "take_dose", {"device_id": medication_device_id(hass, sid)})
    assert _status(hass) == "taken"
    assert _stock(hass) == 28.0
    assert history(entry)[-1]["source"] == "service"


async def test_take_dose_by_id_takes_missed_dose_late(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """A missed dose can be taken late with its dose_id; doing it twice says so."""
    _, sid = await start_hub(hass, freezer)
    dose_id = f"{sid}_20261105_0800"
    for minute in (0, 10, 20, 30, 40):
        await move_to(hass, freezer, local(2026, 11, 5, 8, minute))
    assert _status(hass) == "missed"

    await medtrack(hass, "take_dose", {"dose_id": dose_id})
    assert _status(hass) == "taken_late"
    assert _stock(hass) == 28.0
    assert await _error(hass, "take_dose", {"dose_id": dose_id}) == "dose_unchanged"
    assert await _error(hass, "take_dose", {"dose_id": "nope_1_2"}) == "unknown_dose"


async def test_dose_id_must_match_target(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """A dose_id of another medication than the target is refused."""
    entry, sid = await start_hub(hass, freezer)
    other = _add_medication(hass, entry, "Vitamin D")
    await hass.async_block_till_done()
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    key = await _error(
        hass,
        "take_dose",
        {
            "dose_id": f"{sid}_20261105_0800",
            "device_id": medication_device_id(hass, other),
        },
    )
    assert key == "dose_not_in_target"


async def test_take_without_dose_explains(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Nothing running and nothing in the early window: a clear error."""
    await start_hub(hass, freezer, start=local(2026, 11, 5, 10, 0))
    assert await _error(hass, "take_dose", {"entity_id": STOCK}) == "nothing_to_take"


async def test_snooze_dose_minutes(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """snooze_dose with minutes moves the next reminder by that much."""
    await start_hub(hass, freezer)
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await medtrack(hass, "snooze_dose", {"entity_id": STOCK, "minutes": 30})
    assert _status(hass) == "snoozed"
    reset(notify_calls)
    await move_to(hass, freezer, local(2026, 11, 5, 8, 29))
    assert reminders(notify_calls) == []
    await move_to(hass, freezer, local(2026, 11, 5, 8, 30))
    assert len(reminders(notify_calls)) == 1


async def test_snooze_dose_limit(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """snooze_dose without snoozes left raises (spec: user-visible error)."""
    await start_hub(hass, freezer, options={"max_snoozes": 0})
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    assert await _error(hass, "snooze_dose", {"entity_id": STOCK}) == "no_snoozes_left"


# --- targeting -----------------------------------------------------------------------


def _add_medication(hass: HomeAssistant, entry, name: str) -> str:
    subentry = ConfigSubentry(
        data=MEDICATION_DATA | {CONF_CURRENT_STOCK: 10.0},
        subentry_type=SUBENTRY_MEDICATION,
        title=name,
        unique_id=None,
    )
    hass.config_entries.async_add_subentry(entry, subentry)
    return subentry.subentry_id


async def test_targets_must_be_one_medication(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Refill etc. need exactly one MedTrack medication."""
    entry, sid = await start_hub(hass, freezer)
    other = _add_medication(hass, entry, "Vitamin D")
    await hass.async_block_till_done()

    hub = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    assert await _error(hass, "refill", {"device_id": hub.id}) == "single_medication"
    assert await _error(hass, "refill", {}) == "single_medication"
    assert (
        await _error(
            hass,
            "refill",
            {
                "device_id": [
                    medication_device_id(hass, sid),
                    medication_device_id(hass, other),
                ]
            },
        )
        == "single_medication"
    )
    hass.states.async_set("sensor.outside", "1")
    assert (
        await _error(hass, "refill", {"entity_id": "sensor.outside"})
        == "not_a_medication"
    )
    assert await _error(hass, "refill", {"device_id": "missing"}) == "not_a_medication"

    await medtrack(hass, "refill", {"entity_id": "sensor.vitamin_d_stock"})
    assert float(hass.states.get("sensor.vitamin_d_stock").state) == 70.0
    assert _stock(hass) == 30.0


# --- refill / set_stock ------------------------------------------------------------------


async def test_refill_and_set_stock(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """refill adds the amount (default: refill amount); set_stock corrects."""
    entry, _ = await start_hub(hass, freezer)
    await medtrack(hass, "refill", {"entity_id": STOCK, "amount": 12.5})
    assert _stock(hass) == 42.5
    await medtrack(hass, "refill", {"entity_id": STOCK})
    assert _stock(hass) == 102.5
    await medtrack(hass, "set_stock", {"entity_id": STOCK, "amount": 7})
    assert _stock(hass) == 7.0
    assert hass.states.get("binary_sensor.metformin_low_stock").state == "on"
    assert [(r["type"], r["amount"], r["source"]) for r in history(entry)] == [
        ("refill", 12.5, "service"),
        ("refill", 60.0, "service"),
        ("stock_set", 7.0, "service"),
    ]


async def test_refill_errors(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """No default refill amount, or no stock tracking, gives a clear error."""
    await start_hub(hass, freezer, {CONF_REFILL_AMOUNT: None})
    assert await _error(hass, "refill", {"entity_id": STOCK}) == "no_refill_amount"


async def test_stock_actions_need_stock_tracking(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """refill / set_stock on a medication without stock tracking."""
    await start_hub(hass, freezer, {CONF_CURRENT_STOCK: None})
    target = {"entity_id": "button.metformin_take"}
    assert await _error(hass, "refill", target) == "stock_not_tracked"
    assert (
        await _error(hass, "set_stock", {**target, "amount": 3}) == "stock_not_tracked"
    )


# --- undo_last (spec §12 test 10) --------------------------------------------------------


async def test_undo_last_restores_stock_and_reopens_dose(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Undo of a take inside the reminder window: stock back, pending again,
    reminder shown again."""
    entry, sid = await start_hub(hass, freezer)
    dose_id = f"{sid}_20261105_0800"
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await press(hass, "button.metformin_take")
    assert _stock(hass) == 28.0
    taken_id = history(entry)[-1]["id"]

    reset(notify_calls)
    await move_to(hass, freezer, local(2026, 11, 5, 8, 5))
    await medtrack(hass, "undo_last", {"entity_id": STOCK})
    assert _stock(hass) == 30.0
    assert _status(hass) == "pending"
    [reminder] = reminders(notify_calls)
    assert reminder["data"]["tag"] == f"medtrack_{dose_id}"
    record = history(entry)[-1]
    assert record["type"] == "undo"
    assert record["undone_id"] == taken_id
    assert record["dose_id"] == dose_id
    assert record["state"] == "pending"
    assert record["amount"] == 2.0

    # Reminders resume on their interval; the dose can be taken again.
    reset(notify_calls)
    await move_to(hass, freezer, local(2026, 11, 5, 8, 15))
    assert len(reminders(notify_calls)) == 1
    await press(hass, "button.metformin_take")
    assert _stock(hass) == 28.0

    # Nothing else to undo after this take is undone too.
    await medtrack(hass, "undo_last", {"entity_id": STOCK})
    assert await _error(hass, "undo_last", {"entity_id": STOCK}) == "nothing_to_undo"


async def test_undo_after_window_marks_missed(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Past the reminder window the reverted dose is missed (with Taken late)."""
    _, sid = await start_hub(hass, freezer)
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await press(hass, "button.metformin_take")
    reset(notify_calls)
    await move_to(hass, freezer, local(2026, 11, 5, 9, 0))
    await medtrack(hass, "undo_last", {"entity_id": STOCK})
    assert _status(hass) == "missed"
    [missed] = reminders(notify_calls)
    assert missed["title"] == "Missed: Metformin"
    await medtrack(hass, "take_dose", {"dose_id": f"{sid}_20261105_0800"})
    assert _status(hass) == "taken_late"


async def test_undo_early_take_schedules_again(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Undo of an early take: the dose waits for its time again."""
    await start_hub(hass, freezer, start=local(2026, 11, 5, 7, 30))
    await press(hass, "button.metformin_take")
    await medtrack(hass, "undo_last", {"entity_id": STOCK})
    assert _stock(hass) == 30.0
    assert (
        hass.states.get("sensor.metformin_next_dose").state
        == "2026-11-05T07:00:00+00:00"
    )
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    assert len(reminders(notify_calls)) == 1


async def test_undo_skip_and_refill(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Undo goes back one step at a time: refill, then the skip."""
    await start_hub(hass, freezer)
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await press(hass, "button.metformin_skip")
    await medtrack(hass, "refill", {"entity_id": STOCK, "amount": 10})
    assert _stock(hass) == 40.0

    await medtrack(hass, "undo_last", {"entity_id": STOCK})
    assert _stock(hass) == 30.0
    assert _status(hass) == "skipped"
    await medtrack(hass, "undo_last", {"entity_id": STOCK})
    assert _status(hass) == "pending"
    assert _stock(hass) == 30.0


async def test_undo_keeps_later_stock_correction(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """A stock correction after the take is the counted truth; undo keeps it."""
    await start_hub(hass, freezer)
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await press(hass, "button.metformin_take")
    await medtrack(hass, "set_stock", {"entity_id": STOCK, "amount": 20})
    await medtrack(hass, "undo_last", {"entity_id": STOCK})
    assert _stock(hass) == 20.0
    assert _status(hass) == "pending"


async def test_undo_only_within_24_hours(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Older actions and system decisions (missed, paused) are not undone."""
    await start_hub(hass, freezer)
    await medtrack(hass, "refill", {"entity_id": STOCK, "amount": 10})
    await move_to(hass, freezer, local(2026, 11, 6, 7, 1))
    assert await _error(hass, "undo_last", {"entity_id": STOCK}) == "nothing_to_undo"


async def test_undo_restores_low_stock_alert_state(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Undoing the refill that ended a low-stock period alerts again."""
    _, sid = await start_hub(hass, freezer, {CONF_CURRENT_STOCK: 8.0})
    await medtrack(hass, "refill", {"entity_id": STOCK})
    assert f"medtrack_lowstock_{sid}" in clears(notify_calls)
    reset(notify_calls)
    await medtrack(hass, "undo_last", {"entity_id": STOCK})
    assert [p["title"] for p in reminders(notify_calls)] == ["Low stock: Metformin"]


# --- get_history ---------------------------------------------------------------------


async def test_get_history(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Records come back oldest first, filtered by medication and time."""
    entry, sid = await start_hub(hass, freezer)
    other = _add_medication(hass, entry, "Vitamin D")
    await hass.async_block_till_done()
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await press(hass, "button.metformin_take")
    await press(hass, "button.vitamin_d_take")
    await move_to(hass, freezer, local(2026, 11, 5, 12, 0))
    await medtrack(hass, "refill", {"entity_id": STOCK, "amount": 5})

    everything = await medtrack(hass, "get_history")
    records = everything["records"]
    assert [(r["medication"], r["type"], r.get("state")) for r in records] == [
        ("Metformin", "dose", "pending"),
        ("Vitamin D", "dose", "pending"),
        ("Metformin", "dose", "taken"),
        ("Vitamin D", "dose", "taken"),
        ("Metformin", "refill", None),
    ]
    assert records[2]["medication_id"] == sid

    only_vitamin = await medtrack(
        hass, "get_history", {"device_id": medication_device_id(hass, other)}
    )
    assert {r["medication"] for r in only_vitamin["records"]} == {"Vitamin D"}

    hub = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    assert (
        len((await medtrack(hass, "get_history", {"device_id": hub.id}))["records"])
        == 5
    )

    later = await medtrack(
        hass,
        "get_history",
        {"entity_id": STOCK, "start": (local(2026, 11, 5, 9, 0)).isoformat()},
    )
    assert [r["type"] for r in later["records"]] == ["refill"]

    before = await medtrack(
        hass,
        "get_history",
        {"end": "2026-11-05 08:00:00"},  # local time, as typed in the UI
    )
    assert len(before["records"]) == 4

    start = local(2026, 11, 5, 12, 0)
    assert (
        await _error(
            hass,
            "get_history",
            {
                "start": start.isoformat(),
                "end": (start - timedelta(hours=1)).isoformat(),
            },
        )
        == "invalid_period"
    )


async def test_deleted_medication_history_kept(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """History of a deleted medication stays (name unknown)."""
    entry, sid = await start_hub(hass, freezer)
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await press(hass, "button.metformin_take")
    hass.config_entries.async_remove_subentry(entry, sid)
    await hass.async_block_till_done()
    records = (await medtrack(hass, "get_history"))["records"]
    assert records
    assert {r["medication"] for r in records} == {None}
    assert {r["medication_id"] for r in records} == {sid}


def test_action_descriptions_match_strings() -> None:
    """Every action and field in services.yaml has a name and description."""
    folder = Path(__file__).parents[1] / "custom_components" / DOMAIN
    services = load_yaml_dict(str(folder / "services.yaml"))
    strings = json.loads((folder / "strings.json").read_text())["services"]
    assert (
        set(services)
        == set(strings)
        == {
            "take_dose",
            "skip_dose",
            "snooze_dose",
            "refill",
            "set_stock",
            "undo_last",
            "get_history",
        }
    )
    for name, service in services.items():
        assert strings[name]["name"]
        assert strings[name]["description"]
        assert "target" in service
        for field in (service or {}).get("fields", {}):
            assert strings[name]["fields"][field]["name"]
            assert strings[name]["fields"][field]["description"]


# --- Undo last button and Last action sensor ----------------------------------------


def _last_action(hass: HomeAssistant) -> str:
    return hass.states.get("sensor.metformin_last_action").state


async def test_undo_button_and_last_action(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Last action shows what Undo would revert; each press steps back."""
    entry, _ = await start_hub(hass, freezer)
    assert _last_action(hass) == "None"

    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await move_to(hass, freezer, local(2026, 11, 5, 8, 3))
    await press(hass, "button.metformin_skip")
    assert _last_action(hass) == "Skipped 08:03"

    await move_to(hass, freezer, local(2026, 11, 5, 10, 28))
    await medtrack(hass, "refill", {"entity_id": STOCK, "amount": 30})
    assert _last_action(hass) == "Refilled +30"
    attrs = hass.states.get("sensor.metformin_last_action").attributes
    assert attrs["type"] == "refill"
    assert attrs["record_id"] == history(entry)[-1]["id"]

    await move_to(hass, freezer, local(2026, 11, 5, 20, 0))
    await move_to(hass, freezer, local(2026, 11, 5, 20, 7))
    await press(hass, "button.metformin_take")
    assert _last_action(hass) == "Taken 20:07"

    await press(hass, "button.metformin_undo_last")
    assert _status(hass) == "pending"
    assert history(entry)[-1]["source"] == "button"
    assert _last_action(hass) == "Refilled +30"
    await press(hass, "button.metformin_undo_last")
    assert _stock(hass) == 30.0
    assert _last_action(hass) == "Skipped 08:03"
    await press(hass, "button.metformin_undo_last")
    assert _last_action(hass) == "None"

    with pytest.raises(ServiceValidationError) as err:
        await press(hass, "button.metformin_undo_last")
    assert err.value.translation_key == "nothing_to_undo"


async def test_last_action_taken_late(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """A late take is labelled as such."""
    _, sid = await start_hub(hass, freezer)
    for minute in (0, 10, 20, 30, 40):
        await move_to(hass, freezer, local(2026, 11, 5, 8, minute))
    await move_to(hass, freezer, local(2026, 11, 5, 9, 15))
    await medtrack(hass, "take_dose", {"dose_id": f"{sid}_20261105_0800"})
    assert _last_action(hass) == "Taken late 09:15"


def _no_device_filter(target: dict) -> dict:
    """hassfest: "Services do not support device filters on target"."""
    if "device" in target:
        raise probatio.Invalid("device filter on target")
    return target


# The service schema hassfest applies to custom integrations (HA 2026.10,
# script/hassfest/services.py), built from Home Assistant's own selectors.
_FIELD = probatio.Schema(
    {
        probatio.Optional("example"): object,
        probatio.Optional("default"): object,
        probatio.Optional("required"): bool,
        probatio.Optional("advanced"): bool,
        probatio.Optional("selector"): selector.validate_selector,
        probatio.Optional("name"): str,
        probatio.Optional("description"): str,
    }
)
_SERVICE = probatio.Schema(
    {
        probatio.Required("target"): probatio.All(
            selector.TargetSelector.CONFIG_SCHEMA, _no_device_filter
        ),
        probatio.Optional("fields"): probatio.Schema({str: _FIELD}),
        probatio.Optional("name"): str,
        probatio.Optional("description"): str,
    }
)


def test_services_yaml_passes_hassfest_rules() -> None:
    """Catch what hassfest rejects (e.g. device filters on targets) locally."""
    folder = Path(__file__).parents[1] / "custom_components" / DOMAIN
    services = load_yaml_dict(str(folder / "services.yaml"))
    for name, service in services.items():
        try:
            _SERVICE(service)
        except probatio.Invalid as err:
            pytest.fail(f"{name}: {err}")
    with pytest.raises(probatio.Invalid):
        _SERVICE({"target": {"device": {"integration": DOMAIN}}})
