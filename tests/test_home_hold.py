"""'Remind me at home' (GitHub issue #1): hold a dose until the person is home."""

from __future__ import annotations

from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceCall
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.medtrack.const import (
    CONF_HOME_FALLBACK,
    CONF_HOME_REMINDER,
    CONF_PERSON,
)

from .conftest import (
    TARGETS,
    clears,
    history,
    local,
    medtrack,
    move_to,
    press,
    reminders,
    reset,
    start_hub,
    tap,
)

Calls = dict[str, list[ServiceCall]]
PERSON = "person.alex"
HOME_OPTIONS: dict[str, Any] = {CONF_PERSON: PERSON}


def _set_person(hass: HomeAssistant, state: str) -> None:
    hass.states.async_set(PERSON, state)


async def _arrive(hass: HomeAssistant, state: str = "home") -> None:
    _set_person(hass, state)
    await hass.async_block_till_done()


def _actions(payload: dict) -> list[str]:
    return [a["title"] for a in payload["data"]["actions"]]


async def _start(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    person_state: str | None = "not_home",
    **options: Any,
) -> tuple[MockConfigEntry, str, str]:
    """Hub with a person (away by default); returns entry, sid, 08:00 dose ID."""
    if person_state is not None:
        _set_person(hass, person_state)
    entry, sid = await start_hub(hass, freezer, options=HOME_OPTIONS | options)
    return entry, sid, f"{sid}_20261105_0800"


async def _hold(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, calls: Calls, dose_id: str
) -> None:
    """First reminder at 08:00, 'Remind me at home' tapped at 08:02."""
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await move_to(hass, freezer, local(2026, 11, 5, 8, 2))
    reset(calls)
    await tap(hass, f"MEDTRACK_HOME_{dose_id}", dose_id)


# --- When the button is offered ---------------------------------------------------


async def test_button_offered_while_away(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Away (also in another zone): Taken, Snooze, Remind me at home, Skip."""
    _, _, dose_id = await _start(hass, freezer, person_state="Work")
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    payload = reminders(notify_calls)[0]
    assert _actions(payload) == ["Taken", "Snooze 15 min", "Remind me at home", "Skip"]
    assert payload["data"]["actions"][2]["action"] == f"MEDTRACK_HOME_{dose_id}"
    # Every phone gets the button.
    assert _actions(reminders(notify_calls, TARGETS[1])[0])[2] == "Remind me at home"


async def test_button_checked_per_reminder(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Home at the first reminder, away at the second: only the second has it."""
    await _start(hass, freezer, person_state="home")
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    _set_person(hass, "not_home")
    await move_to(hass, freezer, local(2026, 11, 5, 8, 10))
    first, second = reminders(notify_calls)
    assert "Remind me at home" not in _actions(first)
    assert "Remind me at home" in _actions(second)


async def test_button_not_offered(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Unknown or unavailable person state: no button."""
    await _start(hass, freezer, person_state="unknown")
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    _set_person(hass, "unavailable")
    await move_to(hass, freezer, local(2026, 11, 5, 8, 10))
    for payload in reminders(notify_calls):
        assert _actions(payload) == ["Taken", "Snooze 15 min", "Skip"]


async def test_button_not_offered_when_turned_off(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Hub option 'Offer Remind me at home' off."""
    await _start(hass, freezer, **{CONF_HOME_REMINDER: False})
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    assert "Remind me at home" not in _actions(reminders(notify_calls)[0])


async def test_button_not_offered_without_person(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """No person set: the feature is off."""
    _set_person(hass, "not_home")
    await start_hub(hass, freezer)
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    assert "Remind me at home" not in _actions(reminders(notify_calls)[0])


# --- Holding and coming home ----------------------------------------------------------


async def test_hold_until_home(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Tap: cleared everywhere, no re-reminders or 'missed'; home: re-sent."""
    snoozed = async_capture_events(hass, "medtrack_dose_snoozed")
    entry, _, dose_id = await _start(hass, freezer)
    await _hold(hass, freezer, notify_calls, dose_id)

    for target in TARGETS:
        assert clears(notify_calls, target) == [f"medtrack_{dose_id}"]
    status = hass.states.get("sensor.metformin_status")
    assert status.state == "snoozed"
    assert status.attributes["until"] == "home"
    assert snoozed[-1].data["until"] == "home"
    assert snoozed[-1].data["user_id"] == "user-1"
    record = history(entry)[-1]
    assert record["state"] == "snoozed"
    assert record["until"] == "home"
    assert record["source"] == "notification"

    # Three hours later: still nothing, and not missed.
    await move_to(hass, freezer, local(2026, 11, 5, 11, 0))
    assert reminders(notify_calls) == []
    assert hass.states.get("sensor.metformin_status").state == "snoozed"

    # Arriving home sends the reminder again, with the normal buttons.
    await _arrive(hass)
    (payload,) = reminders(notify_calls)
    assert _actions(payload) == ["Taken", "Snooze 15 min", "Skip"]
    assert payload["message"].endswith("(reminder 1 of 4)")
    status = hass.states.get("sensor.metformin_status")
    assert status.state == "pending"
    assert "until" not in status.attributes

    # Normal behaviour resumes: re-reminders, then missed.
    await move_to(hass, freezer, local(2026, 11, 5, 11, 10))
    assert reminders(notify_calls)[-1]["message"].endswith("(reminder 2 of 4)")


async def test_button_offered_once_per_dose(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """After a hold the dose never offers the button again, even when away."""
    _, _, dose_id = await _start(hass, freezer)
    await _hold(hass, freezer, notify_calls, dose_id)
    await _arrive(hass)
    await _arrive(hass, "not_home")
    await move_to(hass, freezer, local(2026, 11, 5, 8, 20))
    for payload in reminders(notify_calls):
        assert "Remind me at home" not in _actions(payload)


async def test_tap_while_home_reminds_right_away(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Already home when tapping: the reminder comes back at once, no hold."""
    entry, _, dose_id = await _start(hass, freezer)
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    _set_person(hass, "home")
    reset(notify_calls)
    records = len(history(entry))
    await tap(hass, f"MEDTRACK_HOME_{dose_id}", dose_id)

    (payload,) = reminders(notify_calls)
    assert _actions(payload) == ["Taken", "Snooze 15 min", "Skip"]
    assert payload["message"].endswith("(reminder 1 of 4)")
    assert hass.states.get("sensor.metformin_status").state == "pending"
    assert len(history(entry)) == records


async def test_second_tap_is_ignored(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """The same button on the other phone does not start a second hold."""
    entry, _, dose_id = await _start(hass, freezer)
    await _hold(hass, freezer, notify_calls, dose_id)
    records = len(history(entry))
    await tap(hass, f"MEDTRACK_HOME_{dose_id}", dose_id)
    assert len(history(entry)) == records
    assert reminders(notify_calls) == []


async def test_taken_elsewhere_cancels_the_hold(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Taken on the device page while held: done; arriving home sends nothing."""
    _, _, dose_id = await _start(hass, freezer)
    await _hold(hass, freezer, notify_calls, dose_id)
    await press(hass, "button.metformin_take")
    assert hass.states.get("sensor.metformin_status").state == "taken"
    assert "until" not in hass.states.get("sensor.metformin_status").attributes
    await _arrive(hass)
    await move_to(hass, freezer, local(2026, 11, 5, 13, 0))
    assert reminders(notify_calls) == []


async def test_skipped_elsewhere_cancels_the_hold(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Skipped with the action while held."""
    _, _, dose_id = await _start(hass, freezer)
    await _hold(hass, freezer, notify_calls, dose_id)
    await medtrack(hass, "skip_dose", {"dose_id": dose_id})
    assert hass.states.get("sensor.metformin_status").state == "skipped"
    await _arrive(hass)
    assert reminders(notify_calls) == []


async def test_remind_anyway_after(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Still away after 'Remind anyway after' (2 h): reminded, normal again."""
    _, _, dose_id = await _start(hass, freezer, **{CONF_HOME_FALLBACK: 2})
    await _hold(hass, freezer, notify_calls, dose_id)
    await move_to(hass, freezer, local(2026, 11, 5, 10, 1))
    assert reminders(notify_calls) == []
    await move_to(hass, freezer, local(2026, 11, 5, 10, 2))
    (payload,) = reminders(notify_calls)
    assert "Remind me at home" not in _actions(payload)
    assert hass.states.get("sensor.metformin_status").state == "pending"

    # Coming home later changes nothing for this dose.
    await _arrive(hass)
    assert len(reminders(notify_calls)) == 1


async def test_next_dose_makes_a_held_dose_missed(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Held past the next dose (20:00): it becomes missed with 'Taken late'."""
    _, sid, dose_id = await _start(hass, freezer, **{CONF_HOME_FALLBACK: 24})
    await _hold(hass, freezer, notify_calls, dose_id)
    await move_to(hass, freezer, local(2026, 11, 5, 20, 0))
    titles = [p["title"] for p in reminders(notify_calls)]
    assert titles == ["Missed: Metformin", "Medication: Metformin"]
    assert _actions(reminders(notify_calls)[0]) == ["Taken late"]
    assert entry_dose(hass, dose_id).state == "missed"
    assert not entry_dose(hass, dose_id).until_home
    assert f"{sid}_20261105_2000" in reminders(notify_calls)[1]["data"]["tag"]


def entry_dose(hass: HomeAssistant, dose_id: str) -> Any:
    entry = hass.config_entries.async_entries("medtrack")[0]
    return entry.runtime_data.doses[dose_id]


async def test_hold_continues_past_midnight(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Midnight does not end a hold of the 20:00 dose."""
    _, sid, _ = await _start(hass, freezer, **{CONF_HOME_FALLBACK: 6})
    dose_id = f"{sid}_20261105_2000"
    await move_to(hass, freezer, local(2026, 11, 5, 20, 0))
    reset(notify_calls)
    await tap(hass, f"MEDTRACK_HOME_{dose_id}", dose_id)
    await move_to(hass, freezer, local(2026, 11, 6, 0, 30))
    assert entry_dose(hass, dose_id).is_held
    assert reminders(notify_calls) == []

    day = await medtrack(hass, "get_day")
    held = next(d for d in day["doses"] if d["dose_id"] == dose_id)
    assert held["earlier_day"] and held["waiting_for_home"]

    await _arrive(hass)
    (payload,) = reminders(notify_calls)
    assert payload["data"]["tag"] == f"medtrack_{dose_id}"


# --- Settings changed while a dose is held --------------------------------------------


async def test_turning_the_feature_off_releases_holds(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Removing the person reminds a held dose right away."""
    entry, _, dose_id = await _start(hass, freezer)
    await _hold(hass, freezer, notify_calls, dose_id)
    options = {k: v for k, v in entry.options.items() if k != CONF_PERSON}
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.async_block_till_done()
    (payload,) = reminders(notify_calls)
    assert payload["data"]["tag"] == f"medtrack_{dose_id}"
    assert hass.states.get("sensor.metformin_status").state == "pending"


# --- Card / get_day ------------------------------------------------------------------


async def test_day_view_of_a_held_dose(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Waiting for home: Taken and Skip, no Snooze, reminder time = fallback."""
    _, _, dose_id = await _start(hass, freezer)
    await _hold(hass, freezer, notify_calls, dose_id)
    day = await medtrack(hass, "get_day")
    dose = next(d for d in day["doses"] if d["dose_id"] == dose_id)
    assert dose["state"] == "snoozed"
    assert dose["waiting_for_home"]
    assert dose["can_take"] and dose["can_skip"]
    assert not dose["can_snooze"]
    assert dose["next_reminder_at"] == "2026-11-05T12:02:00+01:00"


# --- Restart ---------------------------------------------------------------------------


async def _restart(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, entry: MockConfigEntry, when
) -> None:
    """Stop MedTrack, and start it again at `when` from its stored state."""
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    freezer.move_to(when)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_restart_keeps_the_hold(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Still away after a restart: still held; arriving home re-sends."""
    entry, _, dose_id = await _start(hass, freezer)
    await _hold(hass, freezer, notify_calls, dose_id)
    reset(notify_calls)
    await _restart(hass, freezer, entry, local(2026, 11, 5, 9, 0))
    assert reminders(notify_calls) == []
    assert entry_dose(hass, dose_id).is_held
    assert hass.states.get("sensor.metformin_status").attributes["until"] == "home"

    await _arrive(hass)
    (payload,) = reminders(notify_calls)
    assert payload["data"]["tag"] == f"medtrack_{dose_id}"


async def test_restart_when_home_reminds_right_away(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Came home while HA was off: reminded as soon as MedTrack starts."""
    entry, _, dose_id = await _start(hass, freezer)
    await _hold(hass, freezer, notify_calls, dose_id)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    _set_person(hass, "home")
    reset(notify_calls)
    freezer.move_to(local(2026, 11, 5, 9, 0))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    (payload,) = reminders(notify_calls)
    assert payload["data"]["tag"] == f"medtrack_{dose_id}"
    assert hass.states.get("sensor.metformin_status").state == "pending"


async def test_restart_after_remind_anyway_time(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """'Remind anyway' (12:02) passed while HA was off: reminded on start."""
    entry, _, dose_id = await _start(hass, freezer)
    await _hold(hass, freezer, notify_calls, dose_id)
    reset(notify_calls)
    await _restart(hass, freezer, entry, local(2026, 11, 5, 12, 10))
    (payload,) = reminders(notify_calls)
    assert payload["data"]["tag"] == f"medtrack_{dose_id}"
    assert "Remind me at home" not in _actions(payload)


async def test_restart_long_after_remind_anyway_time(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Off until long after: the dose is missed silently (no burst)."""
    entry, _, dose_id = await _start(hass, freezer)
    await _hold(hass, freezer, notify_calls, dose_id)
    reset(notify_calls)
    await _restart(hass, freezer, entry, local(2026, 11, 5, 15, 0))
    assert reminders(notify_calls) == []
    assert entry_dose(hass, dose_id).state == "missed"


# --- Options form -----------------------------------------------------------------------


async def test_options_form_sets_and_clears_the_person(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """The person is stored when chosen and removed when the picker is cleared."""
    entry, _ = await start_hub(hass, freezer)
    base = {
        k: v
        for k, v in entry.options.items()
        if k not in (CONF_PERSON, CONF_HOME_REMINDER, CONF_HOME_FALLBACK)
    }

    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], base | {CONF_PERSON: PERSON, CONF_HOME_FALLBACK: 6}
    )
    await hass.async_block_till_done()
    assert entry.options[CONF_PERSON] == PERSON
    assert entry.options[CONF_HOME_REMINDER] is True  # default
    assert entry.options[CONF_HOME_FALLBACK] == 6

    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], base | {CONF_PERSON: ""}
    )
    await hass.async_block_till_done()
    assert CONF_PERSON not in entry.options
