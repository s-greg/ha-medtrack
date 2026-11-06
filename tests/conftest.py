"""Shared fixtures for MedTrack tests."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.medtrack.const import (
    CONF_ACTIVE,
    CONF_CRITICAL_MODE,
    CONF_CRITICAL_VOLUME,
    CONF_CURRENT_STOCK,
    CONF_DAYS,
    CONF_DOSE_AMOUNT,
    CONF_EARLY_TAKE_WINDOW,
    CONF_HISTORY_RETENTION,
    CONF_INSTRUCTIONS,
    CONF_INTERRUPTION_LEVEL,
    CONF_LOW_STOCK_THRESHOLD,
    CONF_MAX_REMINDERS,
    CONF_MAX_SNOOZES,
    CONF_NOTIFY_TARGETS,
    CONF_REFILL_AMOUNT,
    CONF_REQUIRE_UNLOCK,
    CONF_REREMIND_INTERVAL,
    CONF_SNOOZE_LENGTH,
    CONF_TIMES,
    CONF_UNIT,
    DOMAIN,
    SUBENTRY_MEDICATION,
    WEEKDAYS,
)

TARGETS = ["mobile_app_phone_a", "mobile_app_phone_b"]

HUB_OPTIONS: dict[str, Any] = {
    CONF_NOTIFY_TARGETS: TARGETS,
    CONF_REREMIND_INTERVAL: 10,
    CONF_MAX_REMINDERS: 4,
    CONF_MAX_SNOOZES: 3,
    CONF_SNOOZE_LENGTH: 15,
    CONF_EARLY_TAKE_WINDOW: 60,
    CONF_INTERRUPTION_LEVEL: "time-sensitive",
    CONF_REQUIRE_UNLOCK: False,
    CONF_CRITICAL_VOLUME: 1.0,
    CONF_HISTORY_RETENTION: 0,
}

MEDICATION_DATA: dict[str, Any] = {
    CONF_DOSE_AMOUNT: 2.0,
    CONF_UNIT: "tablet",
    CONF_TIMES: ["08:00", "20:00"],
    CONF_DAYS: list(WEEKDAYS),
    CONF_INSTRUCTIONS: "with food",
    CONF_CURRENT_STOCK: 30.0,
    CONF_LOW_STOCK_THRESHOLD: 10.0,
    CONF_REFILL_AMOUNT: 60.0,
    CONF_CRITICAL_MODE: "never",
    CONF_ACTIVE: True,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow loading custom_components in every test."""


@pytest.fixture(autouse=True)
def mock_mobile_app_loaded(hass: HomeAssistant) -> None:
    """Pretend mobile_app is set up so HA does not load it (and http, cloud, ...).

    MedTrack only talks to mobile_app through notify services and bus events,
    which the tests fake.
    """
    hass.config.components.add("mobile_app")


@pytest.fixture
def notify_calls(hass: HomeAssistant) -> dict[str, list[ServiceCall]]:
    """Register fake Companion App notify services and capture their calls."""
    return {target: async_mock_service(hass, "notify", target) for target in TARGETS}


def make_hub_entry(
    hass: HomeAssistant,
    medication: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """A hub with one medication 'Metformin'; overrides are merged in."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="MedTrack",
        data={},
        options=HUB_OPTIONS | (options or {}),
        subentries_data=[
            ConfigSubentryData(
                data=MEDICATION_DATA | (medication or {}),
                subentry_type=SUBENTRY_MEDICATION,
                title="Metformin",
                unique_id=None,
            )
        ],
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def hub_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A MedTrack hub with one medication, not yet set up."""
    return make_hub_entry(hass)


# --- Time and action helpers for the scheduler tests ---------------------------

TZ_NAME = "Europe/Amsterdam"


async def use_amsterdam(hass: HomeAssistant) -> None:
    """Run the test in a European time zone with real DST changes."""
    await hass.config.async_set_time_zone(TZ_NAME)


def local(*args: int) -> datetime:
    """Aware datetime in the test time zone, e.g. local(2026, 11, 5, 8, 0)."""
    return datetime(*args, tzinfo=dt_util.get_time_zone(TZ_NAME))


async def move_to(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, when: datetime
) -> None:
    """Jump the clock to `when` and run every timer that became due."""
    freezer.move_to(when)
    async_fire_time_changed(hass, when)
    await hass.async_block_till_done()


async def tap(
    hass: HomeAssistant,
    action: str,
    dose_id: str | None = None,
    user_id: str | None = "user-1",
) -> None:
    """Simulate tapping a notification button on the iPhone."""
    data: dict[str, Any] = {"action": action}
    if dose_id is not None:
        data["action_data"] = {"dose_id": dose_id}
    hass.bus.async_fire(
        "mobile_app_notification_action", data, context=Context(user_id=user_id)
    )
    await hass.async_block_till_done()


def sent(calls: dict[str, list[ServiceCall]], target: str = TARGETS[0]) -> list[dict]:
    """Payloads sent to one phone, in order."""
    return [dict(call.data) for call in calls[target]]


def reminders(
    calls: dict[str, list[ServiceCall]], target: str = TARGETS[0]
) -> list[dict]:
    """Payloads that are real notifications (not clear commands)."""
    return [p for p in sent(calls, target) if p.get("message") != "clear_notification"]


def clears(calls: dict[str, list[ServiceCall]], target: str = TARGETS[0]) -> list[str]:
    """Tags cleared on one phone, in order."""
    return [
        p["data"]["tag"]
        for p in sent(calls, target)
        if p.get("message") == "clear_notification"
    ]


def device_page(hass: HomeAssistant, subentry_id: str) -> str:
    """Path of a medication's device page (opened by tapping a reminder)."""
    entry_id = hass.config_entries.async_entries(DOMAIN)[0].entry_id
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, subentry_id), entry_id
    )
    assert device is not None
    return f"/config/devices/device/{device.id}"


def reset(calls: dict[str, list[ServiceCall]]) -> None:
    """Forget captured calls."""
    for target_calls in calls.values():
        target_calls.clear()


async def start_hub(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    medication: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    start: datetime | None = None,
) -> tuple[MockConfigEntry, str]:
    """Set up a hub with Metformin at `start` (default Thu 5 Nov 07:00)."""
    await use_amsterdam(hass)
    freezer.move_to(start or local(2026, 11, 5, 7, 0))
    entry = make_hub_entry(hass, medication, options)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, next(iter(entry.subentries))


def edit_medication(
    hass: HomeAssistant, entry: MockConfigEntry, subentry_id: str, **changes: Any
) -> None:
    """Save the medication form with some fields changed."""
    subentry = entry.subentries[subentry_id]
    hass.config_entries.async_update_subentry(
        entry, subentry, data={**subentry.data, **changes}
    )


async def press(hass: HomeAssistant, entity_id: str) -> None:
    """Press a button entity (as the user on the device page)."""
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id}, blocking=True
    )


async def medtrack(
    hass: HomeAssistant, service: str, data: dict[str, Any] | None = None
) -> Any:
    """Call a MedTrack action; returns the response of get_history."""
    return await hass.services.async_call(
        DOMAIN,
        service,
        data or {},
        blocking=True,
        return_response=service == "get_history",
    )


def medication_device_id(hass: HomeAssistant, subentry_id: str) -> str:
    """Device ID of a medication."""
    return device_page(hass, subentry_id).rsplit("/", 1)[1]


def history(entry: MockConfigEntry) -> list[dict[str, Any]]:
    """The hub's history records."""
    return entry.runtime_data.history
