"""MedTrack: medication reminders and stock tracking for Home Assistant."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .coordinator import MedTrackConfigEntry, MedTrackManager
from .entity import hub_device_info
from .store import create_store

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: MedTrackConfigEntry) -> bool:
    """Set up a MedTrack hub from a config entry."""
    manager = MedTrackManager(hass, entry)
    await manager.async_setup()
    entry.runtime_data = manager

    # Create the hub device first so medication devices can point to it.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **hub_device_info(manager)
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
