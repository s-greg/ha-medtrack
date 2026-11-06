"""The dashboard card is served by MedTrack and loaded in the frontend."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, Mock

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, UrlManager
from homeassistant.core import HomeAssistant

from custom_components.medtrack.card import CARD_FILE, CARD_URL

from .conftest import make_hub_entry


async def test_card_served_and_loaded(hass: HomeAssistant) -> None:
    """The JS file is served with caching and loaded with a version URL."""
    hass.config.components.update({"frontend", "http"})
    hass.data[DATA_EXTRA_MODULE_URL] = UrlManager(lambda *_: None, [])
    hass.http = Mock(async_register_static_paths=AsyncMock())

    entry = make_hub_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    [paths] = hass.http.async_register_static_paths.call_args.args
    [path] = paths
    assert (path.url_path, path.path, path.cache_headers) == (
        "/medtrack/medtrack-today-card.js",
        str(CARD_FILE),
        True,
    )
    assert CARD_FILE.is_file()
    [url] = hass.data[DATA_EXTRA_MODULE_URL].urls
    # The version plus a hash of the file: a changed file gets a new URL.
    assert re.fullmatch(rf"{CARD_URL}\?v=\d+\.\d+\.\d+\S*-[0-9a-f]{{8}}", url)


async def test_card_skipped_without_frontend(hass: HomeAssistant) -> None:
    """Without the web interface MedTrack still starts."""
    hass.http = None
    entry = make_hub_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
