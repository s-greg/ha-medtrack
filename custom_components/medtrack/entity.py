"""Base entities for MedTrack."""

from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity, EntityDescription

from .const import DOMAIN
from .coordinator import MedTrackManager, signal_update


def hub_device_info(manager: MedTrackManager) -> DeviceInfo:
    """Device representing the MedTrack hub."""
    return DeviceInfo(
        identifiers={(DOMAIN, manager.entry.entry_id)},
        name=manager.entry.title,
        manufacturer="MedTrack",
        model="Hub",
        entry_type=DeviceEntryType.SERVICE,
    )


class MedTrackEntity(Entity):
    """Common behaviour: no polling, refresh on the manager's update signal."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, manager: MedTrackManager, description: EntityDescription
    ) -> None:
        """Store the manager and description."""
        self._manager = manager
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        """Subscribe to state updates."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_update(self._manager.entry.entry_id),
                self.async_write_ha_state,
            )
        )


class MedicationEntity(MedTrackEntity):
    """An entity on a medication's device."""

    def __init__(
        self,
        manager: MedTrackManager,
        subentry_id: str,
        description: EntityDescription,
    ) -> None:
        """Attach to the medication's device."""
        super().__init__(manager, description)
        self._subentry_id = subentry_id
        medication = manager.medications[subentry_id]
        self._attr_unique_id = f"{subentry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry_id)},
            name=medication.name,
            manufacturer="MedTrack",
            model="Medication",
            entry_type=DeviceEntryType.SERVICE,
            via_device_id=dr.async_get_device_id_by_identifier(
                manager.hass,
                (DOMAIN, manager.entry.entry_id),
                config_entry_id=manager.entry.entry_id,
            ),
        )

    @property
    def available(self) -> bool:
        """Unavailable once the medication is deleted (until the entity goes)."""
        return self._subentry_id in self._manager.medications

    async def async_added_to_hass(self) -> None:
        """Subscribe to updates and let the manager track this entity."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._manager.async_register_entity(self._subentry_id, self)
        )


class HubEntity(MedTrackEntity):
    """An entity on the hub device."""

    def __init__(
        self, manager: MedTrackManager, description: EntityDescription
    ) -> None:
        """Attach to the hub device."""
        super().__init__(manager, description)
        self._attr_unique_id = f"{manager.entry.entry_id}_{description.key}"
        self._attr_device_info = hub_device_info(manager)
