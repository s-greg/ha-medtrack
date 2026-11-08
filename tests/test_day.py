"""medtrack.get_day: the data behind the MedTrack Today dashboard card.

Metformin (from conftest): 2 tablets at 08:00 and 20:00, stock 30, low-stock
threshold 10, default refill 60. Hub: reminders every 10 min, max 4, 3
snoozes of 15 min, early-take window 60 min. Thursday 5 November, Amsterdam.
"""

from __future__ import annotations

from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
import pytest

from custom_components.medtrack.const import (
    CONF_ACTIVE,
    CONF_CURRENT_STOCK,
    CONF_TIMES,
    SUBENTRY_MEDICATION,
)

from .conftest import (
    MEDICATION_DATA,
    edit_medication,
    local,
    make_hub_entry,
    medication_device_id,
    medtrack,
    move_to,
    start_hub,
)

Calls = dict[str, list[ServiceCall]]
STOCK = "sensor.metformin_stock"


async def _day(hass: HomeAssistant, **data: Any) -> dict[str, Any]:
    return await medtrack(hass, "get_day", data)


def _dose(day: dict[str, Any], hhmm: str) -> dict[str, Any]:
    return next(d for d in day["doses"] if d["time"] == hhmm)


async def _miss_morning_dose(hass: HomeAssistant, freezer: FrozenDateTimeFactory):
    for minute in (0, 10, 20, 30, 40):
        await move_to(hass, freezer, local(2026, 11, 5, 8, minute))


async def test_day_before_first_dose(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Both doses upcoming, nothing possible yet, full medication details."""
    entry, sid = await start_hub(hass, freezer, start=local(2026, 11, 5, 6, 30))
    day = await _day(hass)

    assert day["entry_id"] == entry.entry_id
    assert day["hub"] == "MedTrack"
    assert day["date"] == "2026-11-05"
    assert day["now"] == "2026-11-05T06:30:00+01:00"
    # The early-take window of the 08:00 dose opens at 07:00.
    assert day["refresh_at"] == "2026-11-05T07:00:00+01:00"
    assert (day["taken"], day["total"]) == (0, 2)
    assert day["snooze_length"] == 15
    assert day["refills"] == []

    assert day["medications"] == [
        {
            "medication_id": sid,
            "name": "Metformin",
            "device_id": medication_device_id(hass, sid),
            "active": True,
            "dose_amount": 2.0,
            "unit": "tablet",
            "unit_plural": "tablets",
            "precision": 0,
            "instructions": "with food",
            "tracks_stock": True,
            "stock": 30.0,
            "stock_text": "30 tablets",
            "low_stock_threshold": 10.0,
            "low_stock": False,
            "days_remaining": 7.5,
            "refill_amount": 60.0,
            "undo": None,
        }
    ]
    assert [d["time"] for d in day["doses"]] == ["08:00", "20:00"]
    assert _dose(day, "08:00") == {
        "dose_id": f"{sid}_20261105_0800",
        "medication_id": sid,
        "medication": "Metformin",
        "time": "08:00",
        "scheduled_at": "2026-11-05T08:00:00+01:00",
        "dose_amount": 2.0,
        "unit": "tablet",
        "quantity": "2 tablets",
        "instructions": "with food",
        "state": "scheduled",
        "taken_at": None,
        "resolved_at": None,
        "reminders_sent": 0,
        "max_reminders": 4,
        "snoozes_used": 0,
        "snoozes_left": 3,
        "next_reminder_at": None,
        "early_from": "2026-11-05T07:00:00+01:00",
        "can_take": False,
        "can_skip": False,
        "can_snooze": False,
        "can_undo": False,
    }


async def test_day_early_window(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Inside the early-take window an upcoming dose can be taken or skipped."""
    await start_hub(hass, freezer, start=local(2026, 11, 5, 7, 30))
    dose = _dose(await _day(hass), "08:00")
    assert dose["state"] == "scheduled"
    assert (dose["can_take"], dose["can_skip"], dose["can_snooze"]) == (
        True,
        True,
        False,
    )
    assert not _dose(await _day(hass), "20:00")["can_take"]


async def test_day_pending_and_snoozes(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """A running dose: reminder count, next reminder, snoozes left."""
    await start_hub(hass, freezer)
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await move_to(hass, freezer, local(2026, 11, 5, 8, 10))
    day = await _day(hass)
    dose = _dose(day, "08:00")
    assert dose["state"] == "pending"
    assert (dose["reminders_sent"], dose["max_reminders"]) == (2, 4)
    assert dose["next_reminder_at"] == "2026-11-05T08:20:00+01:00"
    assert day["refresh_at"] == "2026-11-05T08:20:00+01:00"
    assert (dose["can_take"], dose["can_skip"], dose["can_snooze"]) == (
        True,
        True,
        True,
    )

    for _ in range(3):
        await medtrack(hass, "snooze_dose", {"entity_id": STOCK})
    dose = _dose(await _day(hass), "08:00")
    assert dose["state"] == "snoozed"
    assert (dose["snoozes_used"], dose["snoozes_left"]) == (3, 0)
    assert not dose["can_snooze"]
    assert dose["can_take"]
    assert dose["next_reminder_at"] == "2026-11-05T08:25:00+01:00"


async def test_day_missed_can_be_taken_late(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """A missed dose offers only Taken (late); afterwards it shows when."""
    await start_hub(hass, freezer)
    await _miss_morning_dose(hass, freezer)
    dose = _dose(await _day(hass), "08:00")
    assert dose["state"] == "missed"
    assert dose["resolved_at"] == "2026-11-05T08:40:00+01:00"
    assert (dose["can_take"], dose["can_skip"], dose["can_snooze"]) == (
        True,
        False,
        False,
    )
    assert not dose["can_undo"]  # missing a dose is not undoable

    await move_to(hass, freezer, local(2026, 11, 5, 9, 15))
    await medtrack(hass, "take_dose", {"dose_id": dose["dose_id"]})
    day = await _day(hass)
    dose = _dose(day, "08:00")
    assert dose["state"] == "taken_late"
    assert dose["taken_at"] == "2026-11-05T09:15:00+01:00"
    assert dose["can_undo"] and not dose["can_take"]
    assert day["medications"][0]["undo"]["label"] == "Taken late 09:15"
    assert (day["taken"], day["total"]) == (1, 2)


async def test_day_undo_only_on_what_undo_reverts(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Only the item undo_last would revert has can_undo, with the stock change."""
    await start_hub(hass, freezer)
    await move_to(hass, freezer, local(2026, 11, 5, 8, 5))
    await medtrack(hass, "take_dose", {"entity_id": STOCK})

    day = await _day(hass)
    taken = _dose(day, "08:00")
    assert taken["state"] == "taken"
    assert taken["taken_at"] == "2026-11-05T08:05:00+01:00"
    assert taken["can_undo"]
    undo = day["medications"][0]["undo"]
    assert undo["type"] == "dose"
    assert undo["label"] == "Taken 08:05"
    assert undo["dose_id"] == taken["dose_id"]
    assert undo["at"] == "2026-11-05T08:05:00+01:00"
    assert undo["stock_change"] == 2.0
    assert undo["stock_change_text"] == "2 tablets"
    # Next change on its own: the 20:00 dose's early window at 19:00.
    assert day["refresh_at"] == "2026-11-05T19:00:00+01:00"

    # A refill is now what undo reverts; the dose loses its Undo.
    await medtrack(hass, "refill", {"entity_id": STOCK, "amount": 10})
    day = await _day(hass)
    assert not _dose(day, "08:00")["can_undo"]
    [refill] = day["refills"]
    assert refill["amount"] == 10.0
    assert refill["quantity"] == "10 tablets"
    assert refill["medication"] == "Metformin"
    assert refill["at"] == "2026-11-05T08:05:00+01:00"
    assert refill["can_undo"]
    undo = day["medications"][0]["undo"]
    assert (undo["type"], undo["label"], undo["stock_change"]) == (
        "refill",
        "Refilled +10",
        -10.0,
    )
    assert undo["record_id"] == refill["record_id"]

    # Undoing the refill removes it; the dose is undoable again.
    await medtrack(hass, "undo_last", {"entity_id": STOCK})
    day = await _day(hass)
    assert day["refills"] == []
    assert _dose(day, "08:00")["can_undo"]

    # Skipping the evening dose early moves Undo to that dose.
    await move_to(hass, freezer, local(2026, 11, 5, 19, 30))
    await medtrack(hass, "skip_dose", {"entity_id": STOCK})
    day = await _day(hass)
    assert not _dose(day, "08:00")["can_undo"]
    assert _dose(day, "20:00")["can_undo"]
    assert _dose(day, "20:00")["state"] == "skipped"
    assert day["medications"][0]["undo"]["stock_change"] is None


async def test_day_undo_stock_change_matches_undo(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """The announced stock change is what undo really does."""
    await start_hub(hass, freezer, medication={CONF_CURRENT_STOCK: 5.0})
    # A stock correction after the refill keeps the counted value.
    await medtrack(hass, "refill", {"entity_id": STOCK, "amount": 10})
    await medtrack(hass, "set_stock", {"entity_id": STOCK, "amount": 3})
    day = await _day(hass)
    assert day["medications"][0]["undo"]["stock_change"] is None
    await medtrack(hass, "undo_last", {"entity_id": STOCK})
    assert hass.states.get(STOCK).state == "3.0"

    await medtrack(hass, "refill", {"entity_id": STOCK, "amount": 10})
    day = await _day(hass)
    assert day["medications"][0]["undo"]["stock_change"] == -10.0
    await medtrack(hass, "undo_last", {"entity_id": STOCK})
    assert hass.states.get(STOCK).state == "3.0"


async def test_day_without_stock_tracking(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """No stock: no stock values, and undo changes no stock."""
    await start_hub(hass, freezer, medication={CONF_CURRENT_STOCK: None})
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await medtrack(hass, "take_dose", {"entity_id": "sensor.metformin_status"})
    med = (await _day(hass))["medications"][0]
    assert (med["tracks_stock"], med["stock"], med["days_remaining"]) == (
        False,
        None,
        None,
    )
    assert not med["low_stock"]
    assert med["undo"]["stock_change"] is None


async def test_day_low_stock(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """At the threshold the medication is flagged low."""
    await start_hub(hass, freezer, medication={CONF_CURRENT_STOCK: 10.0})
    med = (await _day(hass))["medications"][0]
    assert med["low_stock"]
    assert (med["stock"], med["low_stock_threshold"]) == (10.0, 10.0)


async def test_day_empty_when_paused(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """A paused medication has no doses today; the card shows its empty state."""
    await start_hub(hass, freezer, medication={CONF_ACTIVE: False})
    day = await _day(hass)
    assert day["doses"] == []
    assert (day["taken"], day["total"]) == (0, 0)
    assert day["medications"][0]["active"] is False
    # Nothing scheduled: the next refresh is midnight.
    assert day["refresh_at"] == "2026-11-06T00:00:00+01:00"


async def test_day_keeps_resolved_doses_of_changed_times(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """A dose taken before its time was removed from the schedule still shows."""
    entry, sid = await start_hub(hass, freezer)
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await medtrack(hass, "take_dose", {"entity_id": STOCK})
    edit_medication(hass, entry, sid, **{CONF_TIMES: ["21:00"]})
    await hass.async_block_till_done()
    day = await _day(hass)
    assert [(d["time"], d["state"]) for d in day["doses"]] == [
        ("08:00", "taken"),
        ("21:00", "scheduled"),
    ]


async def test_day_rolls_over_at_midnight(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """After midnight only the new day's doses are listed."""
    _, sid = await start_hub(hass, freezer)
    await _miss_morning_dose(hass, freezer)
    await move_to(hass, freezer, local(2026, 11, 6, 0, 0))
    day = await _day(hass)
    assert day["date"] == "2026-11-06"
    assert [d["dose_id"] for d in day["doses"]] == [
        f"{sid}_20261106_0800",
        f"{sid}_20261106_2000",
    ]


async def test_day_sorted_by_time_then_name(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Several medications: doses by time, medications by name."""
    entry, _ = await start_hub(hass, freezer)
    hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data=MEDICATION_DATA | {CONF_TIMES: ["08:00"]},
            subentry_type=SUBENTRY_MEDICATION,
            title="Aspirin",
            unique_id=None,
        ),
    )
    await hass.async_block_till_done()
    day = await _day(hass)
    assert [m["name"] for m in day["medications"]] == ["Aspirin", "Metformin"]
    assert [(d["time"], d["medication"]) for d in day["doses"]] == [
        ("08:00", "Aspirin"),
        ("08:00", "Metformin"),
        ("20:00", "Metformin"),
    ]
    assert day["total"] == 3


async def test_get_day_selects_hub(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """One hub: no ID needed. Several: the ID is required and checked."""
    entry, _ = await start_hub(hass, freezer)
    assert (await _day(hass))["entry_id"] == entry.entry_id

    second = make_hub_entry(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()

    for data, key in (({}, "single_hub"), ({"config_entry_id": "nope"}, "unknown_hub")):
        with pytest.raises(ServiceValidationError) as err:
            await _day(hass, **data)
        assert err.value.translation_key == key
    day = await _day(hass, config_entry_id=second.entry_id)
    assert day["entry_id"] == second.entry_id


async def test_day_early_from_only_for_upcoming(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """early_from tells when Take early becomes possible; gone once running."""
    await start_hub(hass, freezer)
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    day = await _day(hass)
    assert _dose(day, "08:00")["early_from"] is None
    assert _dose(day, "20:00")["early_from"] == "2026-11-05T19:00:00+01:00"
