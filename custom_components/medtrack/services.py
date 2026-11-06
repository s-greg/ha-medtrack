"""MedTrack actions (services), spec §9.

Every action targets a medication by its device or by one of its entities.
take_dose and skip_dose may name a dose_id instead. get_history may target
nothing (all medications), a medication, or the hub device (all of its
medications).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.const import ATTR_DEVICE_ID, ATTR_ENTITY_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.util import dt as dt_util
import probatio

from .const import (
    ATTR_AMOUNT,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_DOSE_ID,
    ATTR_END,
    ATTR_MINUTES,
    ATTR_START,
    DOMAIN,
    RANGE_SNOOZE_LENGTH,
    SERVICE_GET_DAY,
    SERVICE_GET_HISTORY,
    SERVICE_REFILL,
    SERVICE_SET_STOCK,
    SERVICE_SKIP_DOSE,
    SERVICE_SNOOZE_DOSE,
    SERVICE_TAKE_DOSE,
    SERVICE_UNDO_LAST,
    ActionVerb,
    Source,
)
from .coordinator import MedTrackManager
from .schedule import subentry_id_from_dose_id

TARGET_FIELDS: dict[Any, Any] = {
    probatio.Optional(ATTR_DEVICE_ID): probatio.All(cv.ensure_list, [str]),
    probatio.Optional(ATTR_ENTITY_ID): cv.entity_ids,
}

DOSE_SCHEMA = probatio.Schema(
    {**TARGET_FIELDS, probatio.Optional(ATTR_DOSE_ID): cv.string}
)
SNOOZE_SCHEMA = probatio.Schema(
    {
        **TARGET_FIELDS,
        probatio.Optional(ATTR_MINUTES): probatio.All(
            probatio.Coerce(int),
            probatio.Range(min=RANGE_SNOOZE_LENGTH[0], max=RANGE_SNOOZE_LENGTH[1]),
        ),
    }
)
REFILL_SCHEMA = probatio.Schema(
    {
        **TARGET_FIELDS,
        probatio.Optional(ATTR_AMOUNT): probatio.All(
            probatio.Coerce(float), probatio.Range(min=0, min_included=False)
        ),
    }
)
SET_STOCK_SCHEMA = probatio.Schema(
    {
        **TARGET_FIELDS,
        probatio.Required(ATTR_AMOUNT): probatio.All(
            probatio.Coerce(float), probatio.Range(min=0)
        ),
    }
)
TARGET_SCHEMA = probatio.Schema(TARGET_FIELDS)
HISTORY_SCHEMA = probatio.Schema(
    {
        **TARGET_FIELDS,
        probatio.Optional(ATTR_START): cv.datetime,
        probatio.Optional(ATTR_END): cv.datetime,
    }
)

DAY_SCHEMA = probatio.Schema({probatio.Optional(ATTR_CONFIG_ENTRY_ID): cv.string})

type Target = tuple[MedTrackManager, str]


def _error(key: str, **placeholders: str) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders or None,
    )


def _managers(hass: HomeAssistant) -> dict[str, MedTrackManager]:
    """Running hubs by config entry ID."""
    return {
        entry.entry_id: entry.runtime_data
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
    }


@callback
def _resolve(hass: HomeAssistant, call: ServiceCall) -> tuple[list[Target], bool]:
    """Medications named by the call's devices and entities.

    Returns the medications and whether anything was targeted at all. The
    hub device stands for all of its medications.
    """
    managers = _managers(hass)
    found: dict[str, Target] = {}
    device_ids: list[str] = list(call.data.get(ATTR_DEVICE_ID, []))
    entity_ids: list[str] = list(call.data.get(ATTR_ENTITY_ID, []))

    dev_reg = dr.async_get(hass)
    for device_id in device_ids:
        device = dev_reg.async_get(device_id)
        manager = managers.get(device.config_entry_id) if device else None
        if device is None or manager is None:
            raise _error("not_a_medication")
        for domain, identifier in device.identifiers:
            if domain != DOMAIN:
                continue
            if identifier in manager.medications:
                found[identifier] = (manager, identifier)
            elif identifier == manager.entry.entry_id:
                found.update((sid, (manager, sid)) for sid in manager.medications)

    ent_reg = er.async_get(hass)
    for entity_id in entity_ids:
        entity = ent_reg.async_get(entity_id)
        manager = managers.get(entity.config_entry_id or "") if entity else None
        if entity is None or manager is None or entity.platform != DOMAIN:
            raise _error("not_a_medication")
        if (sid := entity.config_subentry_id) in manager.medications:
            found[sid] = (manager, sid)
        else:  # a hub entity
            found.update((sid, (manager, sid)) for sid in manager.medications)

    return list(found.values()), bool(device_ids or entity_ids)


def _single(hass: HomeAssistant, call: ServiceCall) -> Target:
    """Exactly one targeted medication."""
    targets, _ = _resolve(hass, call)
    if len(targets) != 1:
        raise _error("single_medication")
    return targets[0]


def _by_dose_id(hass: HomeAssistant, call: ServiceCall, dose_id: str) -> Target:
    """The medication a dose_id belongs to (it must match a given target)."""
    sid = subentry_id_from_dose_id(dose_id)
    for manager in _managers(hass).values():
        if sid in manager.medications and dose_id in manager.doses:
            targets, targeted = _resolve(hass, call)
            if targeted and (manager, sid) not in targets:
                raise _error("dose_not_in_target", dose_id=dose_id)
            return manager, sid
    raise _error("unknown_dose", dose_id=dose_id)


def _dose_action(
    verb: ActionVerb,
    fallback: Callable[[MedTrackManager, str, str | None], Awaitable[object]],
) -> Callable[[ServiceCall], Awaitable[None]]:
    """take_dose / skip_dose: a given dose_id, else like the button."""

    async def handle(call: ServiceCall) -> None:
        user_id = call.context.user_id
        if dose_id := call.data.get(ATTR_DOSE_ID):
            manager, sid = _by_dose_id(call.hass, call, dose_id)
            if not await manager.async_handle_action(
                verb, dose_id, user_id=user_id, source=Source.SERVICE
            ):
                raise _error(
                    "dose_unchanged",
                    medication=manager.medications[sid].name,
                    dose_id=dose_id,
                    state=manager.doses[dose_id].state.value
                    if dose_id in manager.doses
                    else "unknown",
                )
            return
        manager, sid = _single(call.hass, call)
        await fallback(manager, sid, user_id)

    return handle


async def _snooze(call: ServiceCall) -> None:
    manager, sid = _single(call.hass, call)
    await manager.async_snooze(
        sid,
        user_id=call.context.user_id,
        source=Source.SERVICE,
        minutes=call.data.get(ATTR_MINUTES),
    )


async def _refill(call: ServiceCall) -> None:
    manager, sid = _single(call.hass, call)
    await manager.async_refill(
        sid,
        call.data.get(ATTR_AMOUNT),
        user_id=call.context.user_id,
        source=Source.SERVICE,
    )


async def _set_stock(call: ServiceCall) -> None:
    manager, sid = _single(call.hass, call)
    await manager.async_set_stock(
        sid, call.data[ATTR_AMOUNT], user_id=call.context.user_id, source=Source.SERVICE
    )


async def _undo_last(call: ServiceCall) -> None:
    manager, sid = _single(call.hass, call)
    await manager.async_undo_last(
        sid, user_id=call.context.user_id, source=Source.SERVICE
    )


async def _get_history(call: ServiceCall) -> ServiceResponse:
    """History records, oldest first, for the targeted (or all) medications."""
    start = call.data.get(ATTR_START)
    end = call.data.get(ATTR_END)
    start = dt_util.as_utc(start) if start else None
    end = dt_util.as_utc(end) if end else None
    if start and end and start > end:
        raise _error("invalid_period")
    targets, targeted = _resolve(call.hass, call)
    records: list[dict[str, Any]] = []
    if targeted:
        by_manager: dict[str, tuple[MedTrackManager, set[str]]] = {}
        for manager, sid in targets:
            by_manager.setdefault(manager.entry.entry_id, (manager, set()))[1].add(sid)
        for manager, sids in by_manager.values():
            records.extend(manager.history_records(sids, start, end))
    else:
        for manager in _managers(call.hass).values():
            records.extend(manager.history_records(None, start, end))
    records.sort(key=lambda record: dt_util.parse_datetime(record["at"]) or 0)
    return {"records": records}


async def _get_day(call: ServiceCall) -> ServiceResponse:
    """Today's doses of one hub, as shown by the MedTrack Today card."""
    managers = _managers(call.hass)
    if entry_id := call.data.get(ATTR_CONFIG_ENTRY_ID):
        if entry_id not in managers:
            raise _error("unknown_hub")
        return managers[entry_id].day_view()
    if len(managers) != 1:
        raise _error("single_hub")
    return next(iter(managers.values())).day_view()


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the MedTrack actions (once, for all hubs)."""
    register = hass.services.async_register
    register(
        DOMAIN,
        SERVICE_TAKE_DOSE,
        _dose_action(
            ActionVerb.TAKEN,
            lambda m, sid, uid: m.async_take(sid, user_id=uid, source=Source.SERVICE),
        ),
        schema=DOSE_SCHEMA,
    )
    register(
        DOMAIN,
        SERVICE_SKIP_DOSE,
        _dose_action(
            ActionVerb.SKIP,
            lambda m, sid, uid: m.async_skip(sid, user_id=uid, source=Source.SERVICE),
        ),
        schema=DOSE_SCHEMA,
    )
    register(DOMAIN, SERVICE_SNOOZE_DOSE, _snooze, schema=SNOOZE_SCHEMA)
    register(DOMAIN, SERVICE_REFILL, _refill, schema=REFILL_SCHEMA)
    register(DOMAIN, SERVICE_SET_STOCK, _set_stock, schema=SET_STOCK_SCHEMA)
    register(DOMAIN, SERVICE_UNDO_LAST, _undo_last, schema=TARGET_SCHEMA)
    register(
        DOMAIN,
        SERVICE_GET_HISTORY,
        _get_history,
        schema=HISTORY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    register(
        DOMAIN,
        SERVICE_GET_DAY,
        _get_day,
        schema=DAY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
