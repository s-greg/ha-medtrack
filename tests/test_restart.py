"""Persistence and startup recovery (spec §10, §12 test 8, owner rule on bursts)."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceCall
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.medtrack.const import CONF_TIMES

from .conftest import (
    TARGETS,
    local,
    make_hub_entry,
    move_to,
    reminders,
    reset,
    sent,
    tap,
    use_amsterdam,
)

Calls = dict[str, list[ServiceCall]]


async def _start(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, medication: dict | None = None
) -> tuple[MockConfigEntry, str]:
    await use_amsterdam(hass)
    freezer.move_to(local(2026, 11, 5, 7, 0))
    entry = make_hub_entry(hass, medication)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, next(iter(entry.subentries))


async def _stop(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def _restart_at(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, entry: MockConfigEntry, when
) -> None:
    """HA was down until `when`; start MedTrack again from its stored state."""
    freezer.move_to(when)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _history(entry: MockConfigEntry) -> list[dict]:
    return [r for r in entry.runtime_data.history if r["type"] == "dose"]


async def test_short_restart_keeps_the_reminder_timeline(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Down 08:10-08:15: no reminder was due, so the next one comes at 08:20."""
    entry, sid = await _start(hass, freezer)
    dose_id = f"{sid}_20261105_0800"
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await move_to(hass, freezer, local(2026, 11, 5, 8, 10))
    await _stop(hass, entry)
    reset(notify_calls)

    await _restart_at(hass, freezer, entry, local(2026, 11, 5, 8, 15))
    assert reminders(notify_calls) == []
    assert hass.states.get("sensor.metformin_status").state == "pending"

    await move_to(hass, freezer, local(2026, 11, 5, 8, 20))
    msgs = [p["message"] for p in reminders(notify_calls)]
    assert msgs == ["2 tablets · 08:00 · with food (reminder 3 of 4)"]
    assert reminders(notify_calls)[0]["data"]["tag"] == f"medtrack_{dose_id}"


async def test_restart_mid_dose_resumes_immediately(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Down 08:10-08:25 (reminder 3 was due at 08:20): it is sent right away."""
    entry, _ = await _start(hass, freezer)
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await move_to(hass, freezer, local(2026, 11, 5, 8, 10))
    await _stop(hass, entry)
    reset(notify_calls)

    await _restart_at(hass, freezer, entry, local(2026, 11, 5, 8, 25))
    for target in TARGETS:
        assert [p["message"] for p in reminders(notify_calls, target)] == [
            "2 tablets · 08:00 · with food (reminder 3 of 4)"
        ]
    await move_to(hass, freezer, local(2026, 11, 5, 8, 35))
    await move_to(hass, freezer, local(2026, 11, 5, 8, 45))
    titles = [p["title"] for p in reminders(notify_calls)]
    assert titles == [
        "Medication: Metformin",
        "Medication: Metformin",
        "Missed: Metformin",
    ]


async def test_restart_after_window_marks_missed_silently(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Down 08:10-09:00: the window ended at 08:40, so the dose is missed, quietly."""
    entry, sid = await _start(hass, freezer)
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await move_to(hass, freezer, local(2026, 11, 5, 8, 10))
    await _stop(hass, entry)
    reset(notify_calls)
    missed_events = async_capture_events(hass, "medtrack_dose_missed")

    await _restart_at(hass, freezer, entry, local(2026, 11, 5, 9, 0))
    assert sum(len(c) for c in notify_calls.values()) == 0
    assert hass.states.get("sensor.metformin_status").state == "missed"
    assert len(missed_events) == 1
    last = _history(entry)[-1]
    assert last["dose_id"] == f"{sid}_20261105_0800"
    assert last["state"] == "missed"
    assert last["source"] == "system"

    # Taken late still works after the restart.
    await tap(hass, f"MEDTRACK_TAKEN_{sid}_20261105_0800", f"{sid}_20261105_0800")
    assert hass.states.get("sensor.metformin_status").state == "taken_late"


async def test_restart_never_sends_a_burst(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Down 07:00-20:05: 08:00 is missed silently, only 20:00 gets a reminder."""
    entry, sid = await _start(hass, freezer)
    await _stop(hass, entry)
    missed_events = async_capture_events(hass, "medtrack_dose_missed")

    await _restart_at(hass, freezer, entry, local(2026, 11, 5, 20, 5))
    for target in TARGETS:
        sent_now = reminders(notify_calls, target)
        assert len(sent_now) == 1
        assert (
            sent_now[0]["message"] == "2 tablets · 20:00 · with food (reminder 1 of 4)"
        )
    assert [e.data["dose_id"] for e in missed_events] == [f"{sid}_20261105_0800"]
    assert hass.states.get("sensor.metformin_status").state == "pending"


async def test_long_downtime_resumes_only_latest_dose(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Down for two days: older doses missed silently, only today's 08:00 resumes."""
    entry, sid = await _start(hass, freezer)
    await _stop(hass, entry)
    missed_events = async_capture_events(hass, "medtrack_dose_missed")

    await _restart_at(hass, freezer, entry, local(2026, 11, 7, 8, 5))
    assert len(reminders(notify_calls)) == 1
    assert reminders(notify_calls)[0]["data"]["action_data"]["dose_id"] == (
        f"{sid}_20261107_0800"
    )
    # Only the last 48 h are reconstructed: 5 Nov 08:00 is outside that.
    assert [e.data["dose_id"] for e in missed_events] == [
        f"{sid}_20261105_2000",
        f"{sid}_20261106_0800",
        f"{sid}_20261106_2000",
    ]
    assert all(r["source"] == "system" for r in _history(entry))


async def test_stock_and_resolved_doses_survive_restart(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Stock and an early-taken dose persist; no reminder for that dose later."""
    entry, _ = await _start(hass, freezer, {CONF_TIMES: ["08:00"]})
    await move_to(hass, freezer, local(2026, 11, 5, 7, 30))
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.metformin_take"}, blocking=True
    )
    await _stop(hass, entry)
    reset(notify_calls)

    await _restart_at(hass, freezer, entry, local(2026, 11, 5, 7, 45))
    assert hass.states.get("sensor.metformin_stock").state == "28.0"
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    assert sent(notify_calls) == []
    assert hass.states.get("sensor.metformin_taken_today").state == "1"


async def test_stock_edit_in_form_is_logged_correction(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Changing stock in the medication form is applied once and logged."""
    entry, sid = await _start(hass, freezer)
    subentry = entry.subentries[sid]
    hass.config_entries.async_update_subentry(
        entry, subentry, data={**subentry.data, "current_stock": 50.0}
    )
    await hass.async_block_till_done()  # applied in place by the update listener
    assert hass.states.get("sensor.metformin_stock").state == "50.0"
    records = [r for r in entry.runtime_data.history if r["type"] == "stock_set"]
    assert records[-1]["amount"] == 50.0
    assert records[-1]["source"] == "config"

    # A later reload without a change does not log again.
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    records = [r for r in entry.runtime_data.history if r["type"] == "stock_set"]
    assert len(records) == 1
