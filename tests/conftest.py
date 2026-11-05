"""Shared fixtures for MedTrack tests."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant, ServiceCall
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
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


@pytest.fixture
def hub_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A MedTrack hub with one medication, not yet set up."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="MedTrack",
        data={},
        options=HUB_OPTIONS,
        subentries_data=[
            ConfigSubentryData(
                data=MEDICATION_DATA,
                subentry_type=SUBENTRY_MEDICATION,
                title="Metformin",
                unique_id=None,
            )
        ],
    )
    entry.add_to_hass(hass)
    return entry
