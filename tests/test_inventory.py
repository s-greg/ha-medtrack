"""Inventory: days remaining, clamping, low-stock alerts (spec §7, §6, test 7),
the Refill amount / Refill entities and early skip (owner's Phase 3 additions)."""

from __future__ import annotations

from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.medtrack.const import (
    CONF_ACTIVE,
    CONF_CURRENT_STOCK,
    CONF_DAYS,
    CONF_LOW_STOCK_THRESHOLD,
    CONF_REFILL_AMOUNT,
)

from .conftest import (
    TARGETS,
    clears,
    device_page,
    edit_medication,
    history,
    local,
    medtrack,
    move_to,
    press,
    reminders,
    reset,
    start_hub,
)

Calls = dict[str, list[ServiceCall]]


def _low_stock_alerts(calls: Calls, target: str = TARGETS[0]) -> list[dict]:
    return [p for p in reminders(calls, target) if p["title"].startswith("Low stock")]


async def _take_now(hass: HomeAssistant) -> None:
    await press(hass, "button.metformin_take")


def _stock(hass: HomeAssistant) -> float:
    return float(hass.states.get("sensor.metformin_stock").state)


async def _tap_refilled(hass: HomeAssistant, sid: str, alert_id: str | None) -> None:
    data: dict[str, Any] = {"action": f"MEDTRACK_REFILL_{sid}"}
    if alert_id is not None:
        data["action_data"] = {"medication_id": sid, "alert_id": alert_id}
    hass.bus.async_fire(
        "mobile_app_notification_action", data, context=Context(user_id="user-1")
    )
    await hass.async_block_till_done()


# --- Days remaining -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, "7.5"),  # 30 / (2 tablets x 2 times a day)
        ({CONF_DAYS: ["mon", "wed", "fri"]}, "17.5"),  # 30 / (4 x 3/7)
        ({CONF_CURRENT_STOCK: 0.0}, "0.0"),
        ({CONF_ACTIVE: False}, STATE_UNKNOWN),  # paused: no use
    ],
)
async def test_days_remaining(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    notify_calls: Calls,
    changes: dict[str, Any],
    expected: str,
) -> None:
    """Days remaining = stock / average daily use."""
    await start_hub(hass, freezer, changes)
    assert hass.states.get("sensor.metformin_days_remaining").state == expected


async def test_days_remaining_follows_stock(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Taking a dose lowers days remaining."""
    await start_hub(hass, freezer)
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await _take_now(hass)
    assert hass.states.get("sensor.metformin_days_remaining").state == "7.0"


async def test_stock_never_below_zero(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    notify_calls: Calls,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A dose larger than the stock clamps to 0, logs a warning and records
    the amount actually used (so undo restores exactly that)."""
    entry, _ = await start_hub(hass, freezer, {CONF_CURRENT_STOCK: 1.0})
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await _take_now(hass)
    assert _stock(hass) == 0.0
    assert "would go below 0" in caplog.text
    assert history(entry)[-1]["amount"] == 1.0

    await medtrack(hass, "undo_last", {"entity_id": "sensor.metformin_stock"})
    assert _stock(hass) == 1.0


async def test_fractional_doses_do_not_drift(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """0.1 + 0.2 style float noise is rounded away."""
    await start_hub(hass, freezer, {"dose_amount": 0.1, CONF_CURRENT_STOCK: 0.3})
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await _take_now(hass)
    assert _stock(hass) == 0.2


# --- Low-stock alert (spec §12 test 7) --------------------------------------------------


async def test_low_stock_alert_once_per_crossing_and_rearms(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """One alert when stock reaches the threshold, none on later doses; the
    Refilled button adds the default once and re-arms the alert."""
    entry, sid = await start_hub(hass, freezer, {CONF_CURRENT_STOCK: 14.0})
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await _take_now(hass)  # 12: above the threshold of 10
    assert _low_stock_alerts(notify_calls) == []
    assert hass.states.get("binary_sensor.metformin_low_stock").state == STATE_OFF

    await move_to(hass, freezer, local(2026, 11, 5, 20, 0))
    await _take_now(hass)  # 10: at the threshold
    assert hass.states.get("binary_sensor.metformin_low_stock").state == STATE_ON
    alert_id = entry.runtime_data.stock_state[sid].low_stock_alert_id
    assert alert_id
    for target in TARGETS:
        assert _low_stock_alerts(notify_calls, target) == [
            {
                "title": "Low stock: Metformin",
                "message": "10 tablets left · about 2 days. Refilled adds 60 tablets",
                "data": {
                    "tag": f"medtrack_lowstock_{sid}",
                    "group": "medtrack",
                    "push": {"interruption-level": "time-sensitive"},
                    "action_data": {"medication_id": sid, "alert_id": alert_id},
                    "actions": [
                        {"action": f"MEDTRACK_REFILL_{sid}", "title": "Refilled"}
                    ],
                    "url": device_page(hass, sid),
                },
            }
        ]

    # Next dose: still low, but no second alert.
    reset(notify_calls)
    await move_to(hass, freezer, local(2026, 11, 6, 8, 0))
    await _take_now(hass)
    assert _stock(hass) == 8.0
    assert _low_stock_alerts(notify_calls) == []

    # "Refilled" on the phone: +60, alert removed from both phones.
    reset(notify_calls)
    await _tap_refilled(hass, sid, alert_id)
    assert _stock(hass) == 68.0
    assert hass.states.get("binary_sensor.metformin_low_stock").state == STATE_OFF
    for target in TARGETS:
        assert f"medtrack_lowstock_{sid}" in clears(notify_calls, target)
    record = history(entry)[-1]
    assert record["type"] == "refill"
    assert record["amount"] == 60.0
    assert record["source"] == "notification"
    assert record["user_id"] == "user-1"

    # The same tap arriving from the second phone is ignored.
    await _tap_refilled(hass, sid, alert_id)
    assert _stock(hass) == 68.0

    # Re-armed: crossing the threshold again alerts again.
    reset(notify_calls)
    await medtrack(
        hass, "set_stock", {"entity_id": "sensor.metformin_stock", "amount": 12}
    )
    assert _low_stock_alerts(notify_calls) == []
    await move_to(hass, freezer, local(2026, 11, 6, 20, 0))
    await _take_now(hass)
    assert len(_low_stock_alerts(notify_calls)) == 1


async def test_low_stock_without_default_has_no_refilled_action(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Without a default refill amount the alert has no button."""
    _, sid = await start_hub(
        hass, freezer, {CONF_CURRENT_STOCK: 12.0, CONF_REFILL_AMOUNT: None}
    )
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await _take_now(hass)
    [alert] = _low_stock_alerts(notify_calls)
    assert alert["message"] == "10 tablets left · about 2 days"
    assert "actions" not in alert["data"]

    # A stray "Refilled" tap does not add anything.
    await _tap_refilled(hass, sid, None)
    assert _stock(hass) == 10.0


async def test_low_stock_alert_not_repeated_after_restart(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """The "already alerted" flag is stored, so a restart does not re-alert."""
    entry, _ = await start_hub(hass, freezer, {CONF_CURRENT_STOCK: 12.0})
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await _take_now(hass)
    assert len(_low_stock_alerts(notify_calls)) == 1

    reset(notify_calls)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _low_stock_alerts(notify_calls) == []
    assert hass.states.get("binary_sensor.metformin_low_stock").state == STATE_ON


async def test_low_stock_at_setup_and_threshold_changes(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """A medication added with low stock alerts once; removing the threshold
    clears the alert from the phones; setting it again re-alerts."""
    entry, sid = await start_hub(hass, freezer, {CONF_CURRENT_STOCK: 5.0})
    assert len(_low_stock_alerts(notify_calls)) == 1

    reset(notify_calls)
    edit_medication(hass, entry, sid, **{CONF_LOW_STOCK_THRESHOLD: None})
    await hass.async_block_till_done()
    assert clears(notify_calls) == [f"medtrack_lowstock_{sid}"]
    assert hass.states.get("binary_sensor.metformin_low_stock").state == STATE_OFF

    reset(notify_calls)
    edit_medication(hass, entry, sid, **{CONF_LOW_STOCK_THRESHOLD: 10.0})
    await hass.async_block_till_done()
    assert len(_low_stock_alerts(notify_calls)) == 1


# --- Refill amount + Refill button (owner addition 1) ------------------------------------


async def test_refill_button_adds_refill_amount_and_resets_it(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Type an amount, press Refill: stock grows, a refill is logged and the
    box goes back to the default."""
    entry, _ = await start_hub(hass, freezer)
    assert hass.states.get("number.metformin_refill_amount").state == "60"

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.metformin_refill_amount", "value": 25},
        blocking=True,
    )
    assert hass.states.get("number.metformin_refill_amount").state == "25"
    await press(hass, "button.metformin_refill")

    assert _stock(hass) == 55.0
    assert hass.states.get("number.metformin_refill_amount").state == "60"
    record = history(entry)[-1]
    assert record["type"] == "refill"
    assert record["amount"] == 25.0
    assert record["source"] == "button"

    # Pressing again without typing uses the default.
    await press(hass, "button.metformin_refill")
    assert _stock(hass) == 115.0


async def test_refill_button_zero_uses_default(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """0 in the box means "use the default refill amount"."""
    await start_hub(hass, freezer)
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.metformin_refill_amount", "value": 0},
        blocking=True,
    )
    await press(hass, "button.metformin_refill")
    assert _stock(hass) == 90.0


async def test_refill_button_without_any_amount_explains(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """No default and nothing typed: a clear error, nothing changes."""
    entry, _ = await start_hub(hass, freezer, {CONF_REFILL_AMOUNT: None})
    assert hass.states.get("number.metformin_refill_amount").state == STATE_UNKNOWN
    records = len(history(entry))

    with pytest.raises(ServiceValidationError) as err:
        await press(hass, "button.metformin_refill")
    assert err.value.translation_key == "no_refill_amount"
    assert _stock(hass) == 30.0
    assert len(history(entry)) == records

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.metformin_refill_amount", "value": 20},
        blocking=True,
    )
    await press(hass, "button.metformin_refill")
    assert _stock(hass) == 50.0
    assert hass.states.get("number.metformin_refill_amount").state == STATE_UNKNOWN


async def test_changed_default_refill_amount_updates_the_box(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Editing the default in the form puts it in the Refill amount box."""
    entry, sid = await start_hub(hass, freezer)
    edit_medication(hass, entry, sid, **{CONF_REFILL_AMOUNT: 90.0})
    await hass.async_block_till_done()
    assert hass.states.get("number.metformin_refill_amount").state == "90"


# --- Early skip (owner addition 2) --------------------------------------------------------


async def test_early_skip_with_button(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Skip before the dose time records it as skipped; no reminder comes."""
    entry, sid = await start_hub(hass, freezer)
    dose_id = f"{sid}_20261105_0800"
    skipped = async_capture_events(hass, "medtrack_dose_skipped")
    await move_to(hass, freezer, local(2026, 11, 5, 7, 30))
    await press(hass, "button.metformin_skip")

    assert len(skipped) == 1
    record = history(entry)[-1]
    assert record["dose_id"] == dose_id
    assert record["state"] == "skipped"
    assert record["source"] == "button"
    assert hass.states.get("sensor.metformin_status").state == "skipped"
    assert _stock(hass) == 30.0
    assert (
        hass.states.get("sensor.metformin_next_dose").state
        == "2026-11-05T19:00:00+00:00"
    )

    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await move_to(hass, freezer, local(2026, 11, 5, 8, 30))
    assert reminders(notify_calls) == []


async def test_early_skip_with_action_and_dose_id(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """skip_dose works early too, with or without a dose_id."""
    entry, sid = await start_hub(hass, freezer, start=local(2026, 11, 5, 7, 30))
    await medtrack(hass, "skip_dose", {"dose_id": f"{sid}_20261105_0800"})
    assert history(entry)[-1]["state"] == "skipped"
    assert history(entry)[-1]["source"] == "service"

    await move_to(hass, freezer, local(2026, 11, 5, 19, 15))
    await medtrack(hass, "skip_dose", {"entity_id": "button.metformin_skip"})
    assert history(entry)[-1]["dose_id"] == f"{sid}_20261105_2000"


async def test_skip_too_early_explains(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Outside the early-take window Skip says there is nothing to skip."""
    await start_hub(hass, freezer, start=local(2026, 11, 5, 6, 30))
    with pytest.raises(ServiceValidationError) as err:
        await press(hass, "button.metformin_skip")
    assert err.value.translation_key == "nothing_to_skip"


async def test_second_refilled_tap_ignored_while_still_low(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Refilled from two phones adds the amount once, also when stock stays
    low afterwards (so the alert has not re-armed)."""
    entry, sid = await start_hub(
        hass, freezer, {CONF_CURRENT_STOCK: 4.0, CONF_REFILL_AMOUNT: 3.0}
    )
    alert_id = entry.runtime_data.stock_state[sid].low_stock_alert_id
    assert alert_id
    reset(notify_calls)
    await _tap_refilled(hass, sid, alert_id)
    assert _stock(hass) == 7.0
    assert clears(notify_calls) == [f"medtrack_lowstock_{sid}"]
    await _tap_refilled(hass, sid, alert_id)
    assert _stock(hass) == 7.0
    # Still low: no new alert until stock has been above the threshold.
    assert _low_stock_alerts(notify_calls) == []


async def test_refill_amount_display_follows_dose_precision(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Whole numbers for whole doses; decimals when the dose has them."""
    entry, sid = await start_hub(hass, freezer, {CONF_REFILL_AMOUNT: 1.0})
    assert hass.states.get("number.metformin_refill_amount").state == "1"

    edit_medication(hass, entry, sid, dose_amount=0.5, **{CONF_REFILL_AMOUNT: 2.5})
    await hass.async_block_till_done()
    state = hass.states.get("number.metformin_refill_amount")
    assert state.state == "2.5"
    assert state.attributes["step"] == 0.1
