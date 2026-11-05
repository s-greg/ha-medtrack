"""Find Companion App notify targets and their friendly names."""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.util import slugify

from .const import MOBILE_APP_NOTIFY_PREFIX

# Key under which mobile_app stores the phone's name in its config entry data.
_MOBILE_APP_DEVICE_NAME = "device_name"


@callback
def available_notify_targets(hass: HomeAssistant) -> list[str]:
    """Return the Companion App notify services (without the 'notify.' prefix)."""
    return sorted(
        name
        for name in hass.services.async_services_for_domain("notify")
        if name.startswith(MOBILE_APP_NOTIFY_PREFIX)
    )


@callback
def notify_target_labels(hass: HomeAssistant) -> dict[str, str]:
    """Map notify service names to the phone names shown in the Companion App.

    mobile_app names each service slugify("mobile_app_<device name>"), so the
    mapping is rebuilt from its config entries.
    """
    labels: dict[str, str] = {}
    for entry in hass.config_entries.async_entries("mobile_app"):
        if device_name := entry.data.get(_MOBILE_APP_DEVICE_NAME):
            labels[slugify(f"{MOBILE_APP_NOTIFY_PREFIX}{device_name}")] = device_name
    return labels


@callback
def target_label(hass: HomeAssistant, target: str) -> str:
    """Friendly name for one target, falling back to the service name."""
    return notify_target_labels(hass).get(target, target)
