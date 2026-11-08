"""MedTrack: medication reminders and stock tracking for Home Assistant."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .card import async_register_card
from .const import DOMAIN
from .coordinator import MedTrackConfigEntry, MedTrackManager
from .entity import HUB_MANUFACTURER, HUB_MODEL
from .services import async_setup_services
from .store import create_store

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SENSOR,
]

# Set up from the UI only; YAML configuration is not supported.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the MedTrack actions and the dashboard card once, for all hubs."""
    async_setup_services(hass)
    await async_register_card(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: MedTrackConfigEntry) -> bool:
    """Set up a MedTrack hub from a config entry."""
    manager = MedTrackManager(hass, entry)
    await manager.async_setup()
    entry.runtime_data = manager

    # Create the hub device first so medication devices can point to it.
    # Named fields rather than `**DeviceInfo`: from HA 2026.11 DeviceInfo is
    # a class, and unpacking it only works through a temporary compat layer.
    # Keep these in line with entity.hub_device_info (the entities' version).
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer=HUB_MANUFACTURER,
        model=HUB_MODEL,
        entry_type=dr.DeviceEntryType.SERVICE,
    )

    # Medications whose stock tracking was turned off lose those entities.
    for subentry_id in manager.medications:
        manager.async_remove_stock_entities(subentry_id)

    # Adding, changing or removing a medication (subentry) or changing the
    # options fires this listener. Changes are applied in place, without a
    # reload, so entities do not flip to "unavailable".
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: MedTrackConfigEntry
) -> None:
    """Apply a configuration change to the running hub."""
    await entry.runtime_data.async_config_changed()


async def async_remove_entry(hass: HomeAssistant, entry: MedTrackConfigEntry) -> None:
    """Delete the stored doses and history when the hub is deleted."""
    await create_store(hass, entry.entry_id).async_remove()


async def async_unload_entry(hass: HomeAssistant, entry: MedTrackConfigEntry) -> bool:
    """Unload a MedTrack hub."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded
