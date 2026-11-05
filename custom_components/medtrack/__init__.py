"""MedTrack: medication reminders and stock tracking for Home Assistant."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import DOMAIN
from .coordinator import MedTrackConfigEntry, MedTrackManager
from .entity import hub_device_info
from .store import create_store

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]

# Entities that only exist while stock tracking is on: (platform, key).
STOCK_ENTITY_KEYS: tuple[tuple[str, str], ...] = (
    (Platform.SENSOR, "stock"),
    (Platform.SENSOR, "days_remaining"),
    (Platform.BINARY_SENSOR, "low_stock"),
)


async def async_setup_entry(hass: HomeAssistant, entry: MedTrackConfigEntry) -> bool:
    """Set up a MedTrack hub from a config entry."""
    manager = MedTrackManager(hass, entry)
    await manager.async_setup()
    entry.runtime_data = manager

    # Create the hub device first so medication devices can point to it.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **hub_device_info(manager)
    )

    _async_remove_stock_entities(hass, manager)

    # Adding, changing or removing a medication (subentry) or changing the
    # options fires this listener; a reload rebuilds schedules and entities.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


@callback
def _async_remove_stock_entities(hass: HomeAssistant, manager: MedTrackManager) -> None:
    """Remove stock entities of medications whose stock tracking was turned off."""
    ent_reg = er.async_get(hass)
    for subentry_id, medication in manager.medications.items():
        if medication.tracks_stock:
            continue
        for platform, key in STOCK_ENTITY_KEYS:
            if entity_id := ent_reg.async_get_entity_id(
                platform, DOMAIN, f"{subentry_id}_{key}"
            ):
                ent_reg.async_remove(entity_id)


async def _async_update_listener(
    hass: HomeAssistant, entry: MedTrackConfigEntry
) -> None:
    """Reload the entry after its configuration changed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: MedTrackConfigEntry) -> None:
    """Delete the stored doses and history when the hub is deleted."""
    await create_store(hass, entry.entry_id).async_remove()


async def async_unload_entry(hass: HomeAssistant, entry: MedTrackConfigEntry) -> bool:
    """Unload a MedTrack hub."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded
