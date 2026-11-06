"""History retention and diagnostics (spec §10)."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.helpers.redact import REDACTED

from custom_components.medtrack.const import CONF_HISTORY_RETENTION, CONF_NOTIFY_TARGETS
from custom_components.medtrack.diagnostics import async_get_config_entry_diagnostics

from .conftest import TARGETS, history, local, medtrack, move_to, press, start_hub

Calls = dict[str, list[ServiceCall]]


async def test_retention_removes_old_records_at_midnight(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """With 1 day retention, records older than a day go at local midnight."""
    entry, _ = await start_hub(hass, freezer, options={CONF_HISTORY_RETENTION: 1})
    await medtrack(hass, "refill", {"entity_id": "sensor.metformin_stock", "amount": 1})
    old_id = history(entry)[0]["id"]

    await move_to(hass, freezer, local(2026, 11, 6, 0, 0))
    assert old_id in {r["id"] for r in history(entry)}  # 17 h old: kept

    await move_to(hass, freezer, local(2026, 11, 7, 0, 0))
    assert old_id not in {r["id"] for r in history(entry)}
    assert history(entry)  # newer dose records stay


async def test_retention_zero_keeps_everything(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """0 = keep forever."""
    entry, _ = await start_hub(hass, freezer)
    await medtrack(hass, "refill", {"entity_id": "sensor.metformin_stock", "amount": 1})
    first = history(entry)[0]["id"]
    await move_to(hass, freezer, local(2026, 11, 20, 0, 0))
    assert history(entry)[0]["id"] == first


async def test_retention_applied_at_startup(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Lowering the retention takes effect at the next start."""
    entry, _ = await start_hub(hass, freezer, options={CONF_HISTORY_RETENTION: 30})
    await medtrack(hass, "refill", {"entity_id": "sensor.metformin_stock", "amount": 1})
    freezer.move_to(local(2026, 11, 8, 7, 0))
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_HISTORY_RETENTION: 2}
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert all(r["type"] != "refill" for r in history(entry))


async def test_history_survives_restart(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """History is stored, not just kept in memory."""
    entry, _ = await start_hub(hass, freezer)
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))
    await press(hass, "button.metformin_take")
    before = list(history(entry))
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert history(entry)[: len(before)] == before


async def test_diagnostics(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notify_calls: Calls
) -> None:
    """Config, stock, doses and the last 50 records; phones and users redacted."""
    entry, sid = await start_hub(hass, freezer)
    for _ in range(60):
        await medtrack(
            hass, "refill", {"entity_id": "sensor.metformin_stock", "amount": 1}
        )
    await hass.services.async_call(
        "medtrack",
        "set_stock",
        {"entity_id": "sensor.metformin_stock", "amount": 90},
        blocking=True,
        context=Context(user_id="user-secret"),
    )
    await move_to(hass, freezer, local(2026, 11, 5, 8, 0))

    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["options"][CONF_NOTIFY_TARGETS] == REDACTED
    assert diag["entry"]["notify_target_count"] == len(TARGETS)
    assert diag["medications"][sid]["title"] == "Metformin"
    assert diag["medications"][sid]["data"]["instructions"] == "with food"
    assert diag["medications"][sid]["stock"]["stock"] == 90.0
    assert diag["medications"][sid]["days_remaining"] == 22.5
    assert [d["state"] for d in diag["doses"]] == ["pending"]
    assert diag["history_count"] == 62
    assert len(diag["history"]) == 50
    assert diag["history"][-1]["state"] == "pending"
    assert diag["history"][-2]["type"] == "stock_set"
    assert diag["history"][-2]["user_id"] == REDACTED
    text = str(diag)
    assert "user-secret" not in text
    assert not any(target in text for target in TARGETS)
