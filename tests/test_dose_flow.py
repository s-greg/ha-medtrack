"""Scheduler, notifications and actions (spec §12 tests 2, 3, 4, 5, 6 and 9)."""

from __future__ import annotations

from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.medtrack.const import (
    CONF_CRITICAL_MODE,
    CONF_MAX_SNOOZES,
    CONF_NOTIFY_TARGETS,
    CONF_REQUIRE_UNLOCK,
    CONF_TIMES,
    DOMAIN,
)

from .conftest import (
    TARGETS,
    clears,
    local,
    make_hub_entry,
    move_to,
    reminders,
    reset,
    tap,
    use_amsterdam,
)

Calls = dict[str, list[ServiceCall]]

START = local(2026, 11, 5, 7, 0)  # Thursday, CET (UTC+1)


async def _setup(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    medication: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    start=START,
) -> tuple[MockConfigEntry, str]:
    await use_amsterdam(hass)
    freezer.move_to(start)
    entry = make_hub_entry(hass, medication, options)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, next(iter(entry.subentries))


def expected_reminder(
    dose_id: str, number: int, *, snooze: bool = True, push: dict | None = None
) -> dict[str, Any]:
    """The exact reminder payload from spec §6 for the test medication."""
    actions: list[dict[str, Any]] = [
        {"action": f"MEDTRACK_TAKEN_{dose_id}", "title": "Taken"}
    ]
    if snooze:
        actions.append(
            {"action": f"MEDTRACK_SNOOZE_{dose_id}", "title": "Snooze 15 min"}
        )
    actions.append(
        {"action": f"MEDTRACK_SKIP_{dose_id}", "title": "Skip", "destructive": True}
    )
    return {
        "title": "Medication: Metformin",
        "message": f"2 tablet · 08:00 · with food (reminder {number} of 4)",
        "data": {
            "tag": f"medtrack_{dose_id}",
            "group": "medtrack",
            "push": push or {"interruption-level": "time-sensitive"},
            "action_data": {"dose_id": dose_id},
            "actions": actions,
        },
    }


# --- Test 2: reminder at the scheduled local time ---------------------------------


async def test_reminder_fires_at_local_time(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Reminder #1 goes to every phone exactly at 08:00 local time."""
    _, sid = await _setup(hass, freezer)
    dose_id = f"{sid}_20261105_0800"
    assert (
        hass.states.get("sensor.metformin_next_dose").state
        == "2026-11-05T07:00:00+00:00"
    )

    await move_to(hass, freezer, local(2026, 11, 5, 7, 59, 59))
    assert reminders(notify_calls) == []

    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    for target in TARGETS:
        assert reminders(notify_calls, target) == [expected_reminder(dose_id, 1)]
    status = hass.states.get("sensor.metformin_status")
    assert status.state == "pending"
    assert status.attributes["dose_id"] == dose_id
    assert status.attributes["reminders_sent"] == 1


async def test_dst_spring_forward(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """A 02:30 dose on the spring-forward night fires at 03:00 CEST."""
    _, sid = await _setup(
        hass, freezer, {CONF_TIMES: ["02:30"]}, start=local(2026, 3, 29, 0, 0)
    )
    await move_to(hass, freezer, local(2026, 3, 29, 1, 59))
    assert reminders(notify_calls) == []
    await move_to(hass, freezer, local(2026, 3, 29, 3, 0))  # first minute after the gap
    sent = reminders(notify_calls)
    assert len(sent) == 1
    assert sent[0]["data"]["action_data"]["dose_id"] == f"{sid}_20260329_0230"
    assert "· 02:30 ·" in sent[0]["message"]


async def test_dst_fall_back_fires_once(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """A 02:30 dose on the fall-back night fires once, not twice."""
    from datetime import UTC, datetime

    _, sid = await _setup(
        hass, freezer, {CONF_TIMES: ["02:30"]}, start=local(2026, 10, 25, 0, 0)
    )
    dose_id = f"{sid}_20261025_0230"
    await move_to(
        hass, freezer, datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    )  # 02:30 CEST
    assert len(reminders(notify_calls)) == 1
    await tap(hass, f"MEDTRACK_TAKEN_{dose_id}", dose_id)
    reset(notify_calls)
    # The second 02:30 (CET) and the rest of the night: nothing new.
    for minute in (30, 45):
        await move_to(hass, freezer, datetime(2026, 10, 25, 1, minute, tzinfo=UTC))
    await move_to(hass, freezer, datetime(2026, 10, 25, 3, 0, tzinfo=UTC))
    assert reminders(notify_calls) == []


# --- Test 3: re-reminders stop at max, then missed, then taken late ----------------


async def test_rereminders_then_missed_then_taken_late(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """4 reminders 10 min apart with the same tag, then Missed, then Taken late."""
    _, sid = await _setup(hass, freezer)
    dose_id = f"{sid}_20261105_0800"
    missed_events = async_capture_events(hass, "medtrack_dose_missed")
    late_events = async_capture_events(hass, "medtrack_dose_taken_late")

    for minute in (0, 10, 20, 30):
        await move_to(hass, freezer, local(2026, 11, 5, 8, minute))
    assert reminders(notify_calls) == [
        expected_reminder(dose_id, n) for n in (1, 2, 3, 4)
    ]
    reset(notify_calls)

    await move_to(hass, freezer, local(2026, 11, 5, 8, 40))
    missed = {
        "title": "Missed: Metformin",
        "message": "2 tablet · 08:00 · with food",
        "data": {
            "tag": f"medtrack_{dose_id}",
            "group": "medtrack",
            "push": {"interruption-level": "time-sensitive"},
            "action_data": {"dose_id": dose_id},
            "actions": [{"action": f"MEDTRACK_TAKEN_{dose_id}", "title": "Taken late"}],
        },
    }
    for target in TARGETS:
        assert reminders(notify_calls, target) == [missed]
    assert hass.states.get("sensor.metformin_status").state == "missed"
    assert len(missed_events) == 1
    assert missed_events[0].data["dose_id"] == dose_id

    # Nothing further is sent.
    reset(notify_calls)
    await move_to(hass, freezer, local(2026, 11, 5, 9, 30))
    assert reminders(notify_calls) == []

    await tap(hass, f"MEDTRACK_TAKEN_{dose_id}", dose_id)
    assert hass.states.get("sensor.metformin_status").state == "taken_late"
    assert hass.states.get("sensor.metformin_stock").state == "28.0"
    assert len(late_events) == 1
    assert late_events[0].data["user_id"] == "user-1"
    for target in TARGETS:
        assert clears(notify_calls, target) == [f"medtrack_{dose_id}"]


async def test_taken_late_expires_after_24h(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """A missed dose can no longer be taken 24 h after its scheduled time."""
    _, sid = await _setup(hass, freezer, {CONF_TIMES: ["08:00"]})
    dose_id = f"{sid}_20261105_0800"
    for minute in (0, 10, 20, 30, 40):
        await move_to(hass, freezer, local(2026, 11, 5, 8, minute))
    await move_to(hass, freezer, local(2026, 11, 6, 8, 0, 1))
    await tap(hass, f"MEDTRACK_TAKEN_{dose_id}", dose_id)
    assert hass.states.get("sensor.metformin_stock").state == "30.0"


async def test_critical_final_reminder(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Critical mode 'final_reminder' makes only reminder 4 critical, with sound."""
    _, _sid = await _setup(
        hass, freezer, {CONF_CRITICAL_MODE: "final_reminder"}, {"critical_volume": 0.6}
    )
    for minute in (0, 10, 20, 30):
        await move_to(hass, freezer, local(2026, 11, 5, 8, minute))
    pushes = [p["data"]["push"] for p in reminders(notify_calls)]
    assert pushes[:3] == [{"interruption-level": "time-sensitive"}] * 3
    assert pushes[3] == {
        "interruption-level": "critical",
        "sound": {"name": "default", "critical": 1, "volume": 0.6},
    }


async def test_require_unlock_on_taken(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """'Require unlock' adds authenticationRequired to the Taken action only."""
    await _setup(hass, freezer, options={CONF_REQUIRE_UNLOCK: True})
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    actions = reminders(notify_calls)[0]["data"]["actions"]
    assert actions[0]["authenticationRequired"] is True
    assert all("authenticationRequired" not in a for a in actions[1:])


# --- Test 4: snooze --------------------------------------------------------------


async def test_snooze_shifts_next_reminder(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Snooze delays the next reminder by 15 min and does not use a reminder."""
    _, sid = await _setup(hass, freezer)
    dose_id = f"{sid}_20261105_0800"
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await move_to(hass, freezer, local(2026, 11, 5, 8, 2))
    await tap(hass, f"MEDTRACK_SNOOZE_{dose_id}", dose_id)
    assert hass.states.get("sensor.metformin_status").state == "snoozed"
    reset(notify_calls)

    await move_to(hass, freezer, local(2026, 11, 5, 8, 10))  # normal interval: nothing
    assert reminders(notify_calls) == []
    await move_to(hass, freezer, local(2026, 11, 5, 8, 17))  # 08:02 + 15 min
    assert reminders(notify_calls) == [expected_reminder(dose_id, 1)]
    assert hass.states.get("sensor.metformin_status").state == "pending"
    await move_to(hass, freezer, local(2026, 11, 5, 8, 27))
    assert reminders(notify_calls)[-1] == expected_reminder(dose_id, 2)


async def test_snooze_limit_hides_snooze_action(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """After max snoozes the Snooze button disappears and extra snoozes are ignored."""
    _, sid = await _setup(hass, freezer, options={CONF_MAX_SNOOZES: 1})
    dose_id = f"{sid}_20261105_0800"
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await tap(hass, f"MEDTRACK_SNOOZE_{dose_id}", dose_id)
    reset(notify_calls)
    await move_to(hass, freezer, local(2026, 11, 5, 8, 15))
    assert reminders(notify_calls) == [expected_reminder(dose_id, 1, snooze=False)]

    reset(notify_calls)
    await tap(hass, f"MEDTRACK_SNOOZE_{dose_id}", dose_id)  # no snoozes left
    assert hass.states.get("sensor.metformin_status").state == "pending"
    assert clears(notify_calls) == []  # the reminder stays on the phone
    await move_to(hass, freezer, local(2026, 11, 5, 8, 25))
    assert reminders(notify_calls) == [expected_reminder(dose_id, 2, snooze=False)]


# --- Test 5: Taken from the notification -------------------------------------------


async def test_taken_decrements_stock_clears_all_and_is_idempotent(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Taken lowers stock once, clears every phone, and a second tap only clears."""
    _, sid = await _setup(hass, freezer)
    dose_id = f"{sid}_20261105_0800"
    taken_events = async_capture_events(hass, "medtrack_dose_taken")
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    reset(notify_calls)

    await tap(hass, f"MEDTRACK_TAKEN_{dose_id}", dose_id)
    assert hass.states.get("sensor.metformin_status").state == "taken"
    assert hass.states.get("sensor.metformin_stock").state == "28.0"
    assert hass.states.get("sensor.metformin_taken_today").state == "1"
    for target in TARGETS:
        assert [dict(c.data) for c in notify_calls[target]] == [
            {"message": "clear_notification", "data": {"tag": f"medtrack_{dose_id}"}}
        ]
    assert len(taken_events) == 1
    assert taken_events[0].data == {
        "medication": "Metformin",
        "dose_id": dose_id,
        "scheduled_at": "2026-11-05T07:00:00+00:00",
        "at": "2026-11-05T07:00:00+00:00",
        "user_id": "user-1",
    }

    # Second phone taps Taken too: ignored, but still cleared everywhere.
    reset(notify_calls)
    await tap(hass, f"MEDTRACK_TAKEN_{dose_id}", dose_id, user_id="user-2")
    assert hass.states.get("sensor.metformin_stock").state == "28.0"
    assert len(taken_events) == 1
    for target in TARGETS:
        assert clears(notify_calls, target) == [f"medtrack_{dose_id}"]

    # No more reminders for this dose.
    reset(notify_calls)
    await move_to(hass, freezer, local(2026, 11, 5, 8, 40))
    assert reminders(notify_calls) == []


async def test_action_parsed_from_identifier_without_action_data(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Without action_data the dose ID comes from the action identifier."""
    _, sid = await _setup(hass, freezer)
    dose_id = f"{sid}_20261105_0800"
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await tap(hass, f"MEDTRACK_SKIP_{dose_id}")
    assert hass.states.get("sensor.metformin_status").state == "skipped"
    assert hass.states.get("sensor.metformin_stock").state == "30.0"


async def test_foreign_actions_are_ignored(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Other integrations' actions and unknown medications do nothing."""
    await _setup(hass, freezer)
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    reset(notify_calls)
    await tap(hass, "SOMETHING_ELSE")
    await tap(hass, "MEDTRACK_TAKEN_01UNKNOWNMEDICATION_20261105_0800")
    assert hass.states.get("sensor.metformin_status").state == "pending"
    assert sum(len(c) for c in notify_calls.values()) == 0


async def test_buttons_take_skip_snooze(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """The Skip and Snooze buttons act on the running dose."""
    await _setup(hass, freezer)
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.metformin_snooze"}, blocking=True
    )
    assert hass.states.get("sensor.metformin_status").state == "snoozed"
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.metformin_skip"}, blocking=True
    )
    assert hass.states.get("sensor.metformin_status").state == "skipped"
    # Nothing left to act on: pressing again is harmless.
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.metformin_take"}, blocking=True
    )
    assert hass.states.get("sensor.metformin_stock").state == "30.0"


# --- Test 6: early take via the button -------------------------------------------


async def test_early_take_cancels_upcoming_reminder(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Take at 07:30 resolves the 08:00 dose; no reminder is sent at 08:00."""
    _, _sid = await _setup(hass, freezer)
    await move_to(hass, freezer, local(2026, 11, 5, 7, 30))
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.metformin_take"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get("sensor.metformin_status").state == "taken"
    assert hass.states.get("sensor.metformin_stock").state == "28.0"
    assert (
        hass.states.get("sensor.metformin_next_dose").state
        == "2026-11-05T19:00:00+00:00"
    )

    reset(notify_calls)
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await move_to(hass, freezer, local(2026, 11, 5, 8, 10))
    assert reminders(notify_calls) == []


async def test_take_outside_early_window_does_nothing(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Take more than 60 min before the dose does nothing."""
    await _setup(hass, freezer, start=local(2026, 11, 5, 6, 30))
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.metformin_take"}, blocking=True
    )
    assert hass.states.get("sensor.metformin_status").state == "idle"
    assert hass.states.get("sensor.metformin_stock").state == "30.0"


async def test_next_dose_supersedes_running_dose(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """If a dose still runs when the next one starts, the older one is missed."""
    _, sid = await _setup(
        hass, freezer, {CONF_TIMES: ["08:00", "08:15"]}, {CONF_MAX_SNOOZES: 3}
    )
    old = f"{sid}_20261105_0800"
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await move_to(hass, freezer, local(2026, 11, 5, 8, 10))
    await move_to(hass, freezer, local(2026, 11, 5, 8, 15))
    status = hass.states.get("sensor.metformin_status")
    assert status.attributes["dose_id"] == f"{sid}_20261105_0815"
    titles = [p["title"] for p in reminders(notify_calls)]
    assert titles[-2:] == ["Missed: Metformin", "Medication: Metformin"]
    assert reminders(notify_calls)[-2]["data"]["tag"] == f"medtrack_{old}"


# --- Test 9: one phone failing ---------------------------------------------------


async def test_notify_failure_does_not_stop_other_targets(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """A phone that errors is skipped; the other phone and the schedule go on."""

    async def broken(call: ServiceCall) -> None:
        raise HomeAssistantError("push service down")

    hass.services.async_register("notify", TARGETS[0], broken)
    _, sid = await _setup(hass, freezer)
    dose_id = f"{sid}_20261105_0800"

    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await move_to(hass, freezer, local(2026, 11, 5, 8, 10))
    assert reminders(notify_calls, TARGETS[1]) == [
        expected_reminder(dose_id, 1),
        expected_reminder(dose_id, 2),
    ]
    assert hass.states.get("sensor.metformin_status").attributes["reminders_sent"] == 2


async def test_missing_target_raises_repair_issue(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """A phone without a notify service gets a Repairs issue; others still work."""
    entry, _ = await _setup(
        hass, freezer, options={CONF_NOTIFY_TARGETS: [*TARGETS, "mobile_app_old_phone"]}
    )
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    assert len(reminders(notify_calls, TARGETS[0])) == 1
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"missing_target_{entry.entry_id}_mobile_app_old_phone"
    )
    assert issue is not None
    assert issue.translation_placeholders == {"target": "mobile_app_old_phone"}


@pytest.mark.usefixtures("notify_calls")
async def test_rate_budget_issue(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A schedule that could exceed 400 pushes/day raises a Repairs issue."""
    times = [f"{hour:02d}:00" for hour in range(24)]  # 24 x (20 + 10 + 1) = 744
    entry, _ = await _setup(
        hass, freezer, {CONF_TIMES: times}, {"max_reminders": 20, CONF_MAX_SNOOZES: 10}
    )
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"rate_budget_{entry.entry_id}")
    assert issue is not None
    assert issue.translation_placeholders == {"budget": "744"}
