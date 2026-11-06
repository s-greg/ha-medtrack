"""Serve the "MedTrack Today" dashboard card and load it in the frontend.

The card ships inside the integration, so one HACS install gives both. The
JS file is served from this package and added to every frontend page with
`add_extra_js_url` (no entry under Dashboards → Resources is needed, and it
works with YAML dashboards too).

The URL carries `?v=<version>-<file hash>`: browsers and the Companion App
may cache the file for a long time, and any change to the file (an update,
or a test copy with the same version number) gives it a new URL.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CARD_FILE = Path(__file__).parent / "frontend" / "medtrack-today-card.js"
CARD_URL = f"/{DOMAIN}/{CARD_FILE.name}"


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the card file and have the frontend load it (once per start)."""
    if hass.http is None or "frontend" not in hass.config.components:
        return  # no web interface (e.g. some test setups): nothing to load into
    integration = await async_get_integration(hass, DOMAIN)
    digest = await hass.async_add_executor_job(_file_hash, CARD_FILE)
    version = f"{integration.version}-{digest}"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(CARD_FILE), cache_headers=True)]
    )
    add_extra_js_url(hass, f"{CARD_URL}?v={version}")
    _LOGGER.debug("Dashboard card %s loaded as version %s", CARD_URL, version)
