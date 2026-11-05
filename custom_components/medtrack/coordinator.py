"""The MedTrack manager: one per hub config entry, stored on entry.runtime_data.

It owns the dose schedule and state machine (spec §5), sending and clearing
notifications (§6), stock (§7), persistence and restart recovery (§10).

Timing uses a single wake-up timer set to the earliest moment any dose needs
attention; each wake-up processes every due dose and sets the timer again.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, tzinfo
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_time_change,
)
from homeassistant.util import dt as dt_util, ulid as ulid_util

from .const import (
    DEVICE_PAGE_PATH,
    DOMAIN,
    DOSE_RETENTION,
    EVENT_DOSE_PREFIX,
    EVENT_MOBILE_APP_ACTION,
    MAX_RECOVERY,
    RATE_BUDGET_WARNING,
    SAVE_DELAY,
    SCHEDULE_HORIZON,
    STATUS_IDLE,
    STOCK_ENTITY_KEYS,
    SUBENTRY_MEDICATION,
    TAKEN_STATES,
    ActionVerb,
    DoseState,
    Source,
)
from .models import Dose, HubSettings, Medication, display_precision
from .notifications import (
    Notifier,
    dose_tag,
    missed_payload,
    missing_target_issue_id,
    parse_action,
    reminder_payload,
)
from .schedule import (
    daily_push_budget,
    doses_between,
    local_day_bounds,
    subentry_id_from_dose_id,
)
from .store import StoreData, create_store

_LOGGER = logging.getLogger(__name__)

type MedTrackConfigEntry = ConfigEntry[MedTrackManager]


def _medication_config(entry: ConfigEntry) -> dict[str, tuple[str, dict[str, Any]]]:
    """(title, data) of every medication subentry, to detect changes."""
    return {
        sid: (subentry.title, dict(subentry.data))
        for sid, subentry in entry.subentries.items()
        if subentry.subentry_type == SUBENTRY_MEDICATION
    }


def signal_update(entry_id: str) -> str:
    """Dispatcher signal fired when any MedTrack state of this entry changes."""
    return f"{DOMAIN}_{entry_id}_update"


@dataclass(kw_only=True)
class StockState:
    """Persisted stock bookkeeping of one medication."""

    stock: float | None
    # The form value last applied, to detect edits made in the medication form.
    config_stock: float | None
    low_stock_alerted: bool = False


class MedTrackManager:
    """Owns everything MedTrack does for one hub."""

    def __init__(self, hass: HomeAssistant, entry: MedTrackConfigEntry) -> None:
        """Initialise from the config entry and its medication subentries."""
        self.hass = hass
        self.entry = entry
        self.settings = HubSettings.from_options(entry.options)
        self.medications: dict[str, Medication] = {
            subentry_id: Medication.from_subentry(
                subentry_id, subentry.title, subentry.data
            )
            for subentry_id, subentry in entry.subentries.items()
            if subentry.subentry_type == SUBENTRY_MEDICATION
        }
        self.doses: dict[str, Dose] = {}
        self.stock_state: dict[str, StockState] = {}
        self.history: list[dict[str, Any]] = []
        self._last_seen: datetime | None = None
        self._store = create_store(hass, entry.entry_id)
        self._notifier = Notifier(hass, entry.entry_id, self.settings.notify_targets)
        self._lock = asyncio.Lock()
        self._unsub_timer: CALLBACK_TYPE | None = None
        self._unsubs: list[Callable[[], None]] = []
        # Set on shutdown: nothing may run, send or save after that (an
        # unloaded copy must never keep a timer and overwrite newer data).
        self._stopped = False
        # Last applied configuration, to see what an update changed.
        self._title = entry.title
        self._options = dict(entry.options)
        self._subentry_config = _medication_config(entry)
        # Entity platforms register a callback that adds one medication's
        # entities; live entities register themselves per medication.
        self._entity_adders: list[Callable[[str], None]] = []
        self._entities: dict[str, set[Entity]] = {}

    @property
    def tz(self) -> tzinfo:
        """Home Assistant's configured time zone."""
        return dt_util.get_default_time_zone()

    # --- Lifecycle -----------------------------------------------------------------

    async def async_setup(self) -> None:
        """Load state, recover from downtime, build the schedule, start timers."""
        self._load(await self._store.async_load() or {})
        now = dt_util.utcnow()
        for sid in self.medications:
            self._apply_config_stock(sid, now)
        await self._async_drop_unscheduled_doses(now)
        self._recover(now)
        self._extend_horizon(now)
        self._check_rate_budget()
        await self._async_save()

        self._unsubs.append(
            self.hass.bus.async_listen(
                EVENT_MOBILE_APP_ACTION, self._handle_action_event
            )
        )
        self._unsubs.append(
            async_track_time_change(
                self.hass, self._async_midnight, hour=0, minute=0, second=0
            )
        )
        self._schedule_wakeup(now)

    async def async_shutdown(self) -> None:
        """Stop timers and listeners and write the final state.

        A run that is still sending notifications finishes first (we wait for
        the lock); because `_stopped` is set, it cannot set a new timer.
        """
        self._stopped = True
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._cancel_timer()
        async with self._lock:
            self._cancel_timer()
            await self._async_save()

    # --- Configuration changes (no reload) ------------------------------------------

    async def async_config_changed(self) -> None:
        """Apply changed hub settings or medications in place.

        Called by the entry's update listener instead of reloading, so saving
        a form does not make every entity unavailable. Only the entities of a
        medication whose unit, display precision or stock tracking changed are
        re-created.
        """
        entry = self.entry
        async with self._lock:
            if self._stopped:
                return
            now = dt_util.utcnow()
            if entry.title != self._title:
                self._title = entry.title
                self._rename_device(entry.entry_id, entry.title)
            if dict(entry.options) != self._options:
                self._apply_options()
            current = _medication_config(entry)
            previous = self._subentry_config
            removed = [sid for sid in previous if sid not in current]
            added = [sid for sid in current if sid not in previous]
            rebuild = [
                sid
                for sid in current
                if sid in previous
                and current[sid] != previous[sid]
                and await self._async_update_medication(sid, *current[sid], now)
            ]
            for sid in removed:
                await self._async_remove_medication(sid)
            for sid in added:
                self.medications[sid] = Medication.from_subentry(sid, *current[sid])
                self._apply_config_stock(sid, now)
            self._subentry_config = current
            self._extend_horizon(now)
            self._check_rate_budget()
            await self._async_save()
            self._schedule_wakeup(now)

        for sid in (*removed, *rebuild):
            await self._async_remove_entities(sid)
        for sid in (*rebuild, *added):
            self.async_add_medication_entities(sid)
        self.async_notify_entities()

    def _apply_options(self) -> None:
        """Use new hub settings from now on (running doses keep their timers)."""
        old_targets = set(self.settings.notify_targets)
        self._options = dict(self.entry.options)
        self.settings = HubSettings.from_options(self.entry.options)
        self._notifier = Notifier(
            self.hass, self.entry.entry_id, self.settings.notify_targets
        )
        for target in old_targets - set(self.settings.notify_targets):
            ir.async_delete_issue(
                self.hass, DOMAIN, missing_target_issue_id(self.entry.entry_id, target)
            )

    async def _async_update_medication(
        self, sid: str, title: str, data: dict[str, Any], now: datetime
    ) -> bool:
        """Apply an edited medication. Returns True if its entities must be
        re-created (unit, display precision or stock tracking changed)."""
        old = self.medications[sid]
        new = Medication.from_subentry(sid, title, data)
        self.medications[sid] = new
        self._apply_config_stock(sid, now)
        if new.name != old.name:
            self._rename_device(sid, new.name)
        if old.active and not new.active:
            await self._async_end_doses_for_pause(new, now)
        if (new.times, new.days, new.active) != (old.times, old.days, old.active):
            # Rebuild the not-yet-started doses; running, taken, skipped and
            # missed doses stay as they are.
            for dose in list(self.doses.values()):
                if dose.subentry_id == sid and dose.state == DoseState.SCHEDULED:
                    del self.doses[dose.dose_id]
        return (
            new.unit != old.unit
            or new.tracks_stock != old.tracks_stock
            or display_precision(new.dose_amount) != display_precision(old.dose_amount)
        )

    async def _async_remove_medication(self, sid: str) -> None:
        """Forget a deleted medication: its doses, alerts and stock."""
        for dose in list(self.doses.values()):
            if dose.subentry_id != sid:
                continue
            if dose.is_active or dose.state == DoseState.MISSED:
                await self._notifier.async_clear(dose_tag(dose.dose_id))
            del self.doses[dose.dose_id]
        self.medications.pop(sid, None)
        self.stock_state.pop(sid, None)

    @callback
    def _rename_device(self, identifier: str, name: str) -> None:
        dev_reg = dr.async_get(self.hass)
        if device := dev_reg.async_get_device_by_identifier(
            (DOMAIN, identifier), self.entry.entry_id
        ):
            dev_reg.async_update_device(device.id, name=name)

    # --- Entity bookkeeping ----------------------------------------------------------

    @callback
    def async_register_entity_adder(self, add: Callable[[str], None]) -> None:
        """A platform's callback that adds the entities of one medication."""
        self._entity_adders.append(add)

    @callback
    def async_add_medication_entities(self, sid: str) -> None:
        """Create the entities of one medication on every platform."""
        if sid not in self.medications:
            return
        self.async_remove_stock_entities(sid)
        for add in self._entity_adders:
            add(sid)

    @callback
    def async_register_entity(self, sid: str, entity: Entity) -> CALLBACK_TYPE:
        """Track a live medication entity; returns the unregister callback."""
        self._entities.setdefault(sid, set()).add(entity)
        return lambda: self._entities.get(sid, set()).discard(entity)

    async def _async_remove_entities(self, sid: str) -> None:
        """Remove the live entity objects of one medication (registry kept)."""
        for entity in list(self._entities.pop(sid, ())):
            await entity.async_remove(force_remove=True)

    @callback
    def async_remove_stock_entities(self, sid: str) -> None:
        """Delete stock entities from the registry when stock is not tracked."""
        med = self.medications.get(sid)
        if med is None or med.tracks_stock:
            return
        ent_reg = er.async_get(self.hass)
        for platform, key in STOCK_ENTITY_KEYS:
            if entity_id := ent_reg.async_get_entity_id(
                platform, DOMAIN, f"{sid}_{key}"
            ):
                ent_reg.async_remove(entity_id)

    # --- Persistence -----------------------------------------------------------------

    def _load(self, data: StoreData) -> None:
        self._last_seen = (
            datetime.fromisoformat(data["last_seen"]) if data.get("last_seen") else None
        )
        self.doses = {
            raw["dose_id"]: Dose.from_dict(raw) for raw in data.get("doses", [])
        }
        self.stock_state = {
            sid: StockState(**raw) for sid, raw in data.get("medications", {}).items()
        }
        self.history = list(data.get("history", []))

    @callback
    def _data(self) -> StoreData:
        """Snapshot for the Store; last_seen is the moment of writing."""
        return {
            "last_seen": dt_util.utcnow().isoformat(),
            "doses": [
                dose.as_dict()
                for dose in self.doses.values()
                if dose.state != DoseState.SCHEDULED
            ],
            "medications": {
                sid: asdict(state)
                for sid, state in self.stock_state.items()
                if sid in self.medications
            },
            "history": self.history,
        }

    async def _async_save(self) -> None:
        """Write now (used after every state transition)."""
        await self._store.async_save(self._data())

    @callback
    def _schedule_save(self) -> None:
        """Write soon (routine changes)."""
        self._store.async_delay_save(self._data, SAVE_DELAY)

    # --- Setup helpers -----------------------------------------------------------------

    def _apply_config_stock(self, sid: str, now: datetime) -> None:
        """Create stock state, and apply a stock edit made in the medication form.

        An edited form value is a logged stock correction (like set_stock), not
        a reset; an empty value turns stock tracking off.
        """
        med = self.medications[sid]
        state = self.stock_state.get(sid)
        if state is None:
            self.stock_state[sid] = StockState(
                stock=med.initial_stock, config_stock=med.initial_stock
            )
            return
        if med.initial_stock == state.config_stock:
            return
        if med.initial_stock is not None and med.initial_stock != state.stock:
            self._add_history(
                "stock_set", sid, now, source=Source.CONFIG, amount=med.initial_stock
            )
            _LOGGER.info("Stock of %s corrected to %s", med.name, med.initial_stock)
        state.stock = med.initial_stock
        state.config_stock = med.initial_stock

    async def _async_drop_unscheduled_doses(self, now: datetime) -> None:
        """Forget doses of removed medications; end running doses of paused ones."""
        for med in self.medications.values():
            if not med.active:
                await self._async_end_doses_for_pause(med, now)
        for dose in list(self.doses.values()):
            if dose.subentry_id in self.medications:
                continue
            if dose.is_active or dose.state == DoseState.MISSED:
                await self._notifier.async_clear(dose_tag(dose.dose_id))
            del self.doses[dose.dose_id]

    async def _async_end_doses_for_pause(self, med: Medication, now: datetime) -> None:
        """Pausing: a running dose is recorded as skipped (source system), its
        notification cleared; doses not started yet are dropped. Missed doses
        keep their "Taken late" option."""
        for dose in list(self.doses.values()):
            if dose.subentry_id != med.subentry_id:
                continue
            if dose.is_active:
                self._transition(
                    med,
                    dose,
                    DoseState.SKIPPED,
                    now,
                    source=Source.SYSTEM,
                    reason="paused",
                )
                dose.next_action_at = dose.scheduled_at + DOSE_RETENTION
                await self._notifier.async_clear(dose_tag(dose.dose_id))
            elif dose.state == DoseState.SCHEDULED:
                del self.doses[dose.dose_id]

    def _recover(self, now: datetime) -> None:
        """Handle doses that came due while HA was not running (spec §10).

        Per medication, only the most recent dose still inside its reminder
        window is resumed (its next reminder goes out right away). Every older
        dose, and the newest one if its window has passed, is marked missed
        silently: no notifications, so a restart never causes a burst.
        """
        known_ids = {record.get("dose_id") for record in self.history}
        start = None
        if self._last_seen is not None:
            start = max(self._last_seen, now - MAX_RECOVERY)

        for sid, med in self.medications.items():
            if not med.active:
                continue
            candidates = [
                dose
                for dose in self.doses.values()
                if dose.subentry_id == sid and dose.is_active
            ]
            if start is not None and start < now:
                for scheduled in doses_between(med, start, now, self.tz):
                    if (
                        scheduled.dose_id in self.doses
                        or scheduled.dose_id in known_ids
                    ):
                        continue
                    dose = Dose(
                        dose_id=scheduled.dose_id,
                        subentry_id=sid,
                        local_time=scheduled.local_time,
                        scheduled_at=scheduled.scheduled_at,
                        next_action_at=scheduled.scheduled_at,
                    )
                    self.doses[dose.dose_id] = dose
                    candidates.append(dose)
            if not candidates:
                continue
            candidates.sort(key=lambda dose: dose.scheduled_at)
            newest = candidates.pop()
            if not self._in_window(med, newest, now):
                candidates.append(newest)
            for dose in candidates:
                self._mark_missed_silently(med, dose, now)

    def _in_window(self, med: Medication, dose: Dose, now: datetime) -> bool:
        """Whether a dose would not yet have become missed by `now`."""
        if dose.next_action_at is None:
            return False
        interval = self._interval(med)
        remaining = self._max_reminders(med) - dose.reminders_sent
        if dose.state == DoseState.SNOOZED:
            remaining += 1  # the re-send after a snooze does not count
        return now < dose.next_action_at + interval * remaining

    def _mark_missed_silently(self, med: Medication, dose: Dose, now: datetime) -> None:
        self._transition(med, dose, DoseState.MISSED, now, source=Source.SYSTEM)
        expires = dose.scheduled_at + DOSE_RETENTION
        if expires <= now:
            del self.doses[dose.dose_id]
        else:
            dose.next_action_at = expires

    def _extend_horizon(self, now: datetime) -> None:
        """Create timers for every dose in the next 48 hours."""
        for med in self.medications.values():
            for scheduled in doses_between(med, now, now + SCHEDULE_HORIZON, self.tz):
                if scheduled.dose_id in self.doses:
                    continue
                self.doses[scheduled.dose_id] = Dose(
                    dose_id=scheduled.dose_id,
                    subentry_id=med.subentry_id,
                    local_time=scheduled.local_time,
                    scheduled_at=scheduled.scheduled_at,
                    next_action_at=scheduled.scheduled_at,
                )

    def _check_rate_budget(self) -> None:
        """Warn (log + Repairs) if the worst case could hit Apple's push limit."""
        budget = daily_push_budget(
            list(self.medications.values()),
            self.settings.max_reminders,
            self.settings.max_snoozes,
        )
        issue_id = f"rate_budget_{self.entry.entry_id}"
        if budget <= RATE_BUDGET_WARNING:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        _LOGGER.warning(
            "Worst case %d notifications per phone per day exceeds %d "
            "(the limit is 500); reduce reminders or snoozes",
            budget,
            RATE_BUDGET_WARNING,
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="rate_budget",
            translation_placeholders={"budget": str(budget)},
        )

    # --- Timer ----------------------------------------------------------------

    @callback
    def _schedule_wakeup(self, now: datetime) -> None:
        """Set the single timer to the earliest pending action."""
        self._cancel_timer()
        if self._stopped:
            return
        times = [d.next_action_at for d in self.doses.values() if d.next_action_at]
        if not times:
            return
        earliest = min(times)
        if earliest <= now:
            self.entry.async_create_task(
                self.hass, self._async_process(), "medtrack process due doses"
            )
        else:
            self._unsub_timer = async_track_point_in_utc_time(
                self.hass, self._async_on_timer, earliest
            )

    @callback
    def _cancel_timer(self) -> None:
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None

    async def _async_on_timer(self, _now: datetime) -> None:
        self._unsub_timer = None
        await self._async_process()

    async def _async_process(self) -> None:
        """Advance every dose whose next action is due."""
        async with self._lock:
            if self._stopped:
                return
            now = dt_util.utcnow()
            due = sorted(
                (
                    dose
                    for dose in self.doses.values()
                    if dose.next_action_at is not None and dose.next_action_at <= now
                ),
                key=lambda dose: dose.scheduled_at,
            )
            for dose in due:
                if dose.dose_id in self.doses:  # may be gone (superseded)
                    await self._async_advance(dose, now)
            if due:
                await self._async_save()
            self._schedule_wakeup(now)
        self.async_notify_entities()

    async def _async_midnight(self, _now: datetime) -> None:
        """Roll the schedule forward and reset daily counters."""
        async with self._lock:
            if self._stopped:
                return
            now = dt_util.utcnow()
            self._extend_horizon(now)
            self._schedule_save()
            self._schedule_wakeup(now)
        self.async_notify_entities()

    # --- State machine -------------------------------------------------------------

    def _interval(self, med: Medication) -> timedelta:
        return timedelta(
            minutes=med.reremind_interval or self.settings.reremind_interval
        )

    def _max_reminders(self, med: Medication) -> int:
        return med.max_reminders or self.settings.max_reminders

    async def _async_advance(self, dose: Dose, now: datetime) -> None:
        """Do whatever is due for one dose."""
        med = self.medications.get(dose.subentry_id)
        if med is None:
            del self.doses[dose.dose_id]
            return
        max_reminders = self._max_reminders(med)

        if dose.state == DoseState.SCHEDULED:
            await self._async_supersede(med, dose, now)
            self._transition(med, dose, DoseState.PENDING, now, source=Source.SYSTEM)
            dose.reminders_sent = 1
            dose.next_action_at = now + self._interval(med)
            await self._async_send_reminder(med, dose)
        elif dose.state == DoseState.PENDING:
            if dose.reminders_sent < max_reminders:
                dose.reminders_sent += 1
                dose.next_action_at = now + self._interval(med)
                await self._async_send_reminder(med, dose)
            else:
                await self._async_mark_missed(med, dose, now)
        elif dose.state == DoseState.SNOOZED:
            # The reminder after a snooze repeats the current number.
            self._transition(med, dose, DoseState.PENDING, now, source=Source.SYSTEM)
            dose.next_action_at = now + self._interval(med)
            await self._async_send_reminder(med, dose)
        else:
            # Resolved or missed and past the 24 h window: forget it.
            del self.doses[dose.dose_id]
            if dose.state == DoseState.MISSED:
                await self._notifier.async_clear(dose_tag(dose.dose_id))

    async def _async_send_reminder(self, med: Medication, dose: Dose) -> None:
        await self._notifier.async_send(
            reminder_payload(
                med,
                dose,
                self.settings,
                number=max(dose.reminders_sent, 1),
                max_reminders=self._max_reminders(med),
                url=self.tap_url(med.subentry_id),
            )
        )

    def tap_url(self, sid: str) -> str | None:
        """Page opened by tapping a notification: the hub option, else the
        medication's device page (where its buttons are)."""
        if self.settings.tap_path:
            return self.settings.tap_path
        device = dr.async_get(self.hass).async_get_device_by_identifier(
            (DOMAIN, sid), self.entry.entry_id
        )
        return None if device is None else DEVICE_PAGE_PATH.format(device_id=device.id)

    async def _async_supersede(self, med: Medication, new: Dose, now: datetime) -> None:
        """An older dose still running when the next one starts becomes missed."""
        for dose in list(self.doses.values()):
            if (
                dose.subentry_id == med.subentry_id
                and dose.is_active
                and dose.scheduled_at < new.scheduled_at
            ):
                await self._async_mark_missed(med, dose, now)

    async def _async_mark_missed(
        self, med: Medication, dose: Dose, now: datetime
    ) -> None:
        """Mark missed and send the 'Missed' notification with 'Taken late'."""
        self._mark_missed_silently(med, dose, now)
        await self._notifier.async_send(
            missed_payload(med, dose, self.settings, url=self.tap_url(med.subentry_id))
        )

    def _transition(
        self,
        med: Medication,
        dose: Dose,
        state: DoseState,
        at: datetime,
        *,
        source: Source,
        user_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Change state, fire medtrack_dose_<state> and append a history record."""
        extra = {} if reason is None else {"reason": reason}
        dose.state = state
        if state not in (DoseState.PENDING, DoseState.SNOOZED, DoseState.SCHEDULED):
            dose.resolved_at = at
        self.hass.bus.async_fire(
            f"{EVENT_DOSE_PREFIX}{state}",
            {
                "medication": med.name,
                "dose_id": dose.dose_id,
                "scheduled_at": dose.scheduled_at.isoformat(),
                "at": at.isoformat(),
                "user_id": user_id,
            },
        )
        self._add_history(
            "dose",
            med.subentry_id,
            at,
            source=source,
            user_id=user_id,
            dose_id=dose.dose_id,
            scheduled_at=dose.scheduled_at.isoformat(),
            state=state.value,
            **extra,
        )

    def _add_history(
        self,
        record_type: str,
        subentry_id: str,
        at: datetime,
        *,
        source: Source,
        user_id: str | None = None,
        **fields: Any,
    ) -> None:
        """Append a history record (spec §10)."""
        record: dict[str, Any] = {
            "id": ulid_util.ulid_now(),
            "type": record_type,
            "medication_id": subentry_id,
            "at": at.isoformat(),
            "user_id": user_id,
            "source": source.value,
        }
        record.update(fields)
        self.history.append(record)

    # --- Actions -------------------------------------------------------------------

    @callback
    def _handle_action_event(self, event: Event) -> None:
        """React to a tapped MedTrack notification button."""
        parsed = parse_action(str(event.data.get("action", "")))
        if parsed is None or self._stopped:
            return
        verb, ident = parsed
        action_data = event.data.get("action_data")
        dose_id = (
            action_data.get("dose_id") if isinstance(action_data, dict) else None
        ) or ident
        if subentry_id_from_dose_id(dose_id) not in self.medications:
            return  # another hub's dose, or a removed medication
        self.entry.async_create_task(
            self.hass,
            self.async_handle_action(
                verb, dose_id, user_id=event.context.user_id, source=Source.NOTIFICATION
            ),
            "medtrack notification action",
        )

    async def async_handle_action(
        self,
        verb: ActionVerb,
        dose_id: str,
        *,
        user_id: str | None,
        source: Source,
        snooze_minutes: int | None = None,
    ) -> bool:
        """Apply Taken / Snooze / Skip to a specific dose.

        Returns True if the dose changed. An action for a dose that is already
        resolved (for example tapped on a second phone) only clears the
        notification on all phones.
        """
        async with self._lock:
            if self._stopped:
                return False
            now = dt_util.utcnow()
            changed = await self._async_apply(
                verb,
                dose_id,
                now,
                user_id=user_id,
                source=source,
                snooze_minutes=snooze_minutes,
            )
            if changed:
                await self._async_save()
                self._schedule_wakeup(now)
            elif self._is_finished(dose_id):
                # Duplicate or stale tap: just make sure every phone is clean.
                await self._notifier.async_clear(dose_tag(dose_id))
        if changed:
            self.async_notify_entities()
        return changed

    async def _async_apply(
        self,
        verb: ActionVerb,
        dose_id: str,
        now: datetime,
        *,
        user_id: str | None,
        source: Source,
        snooze_minutes: int | None,
    ) -> bool:
        dose = self.doses.get(dose_id)
        med = self.medications.get(subentry_id_from_dose_id(dose_id))
        if dose is None or med is None:
            return False

        if verb == ActionVerb.TAKEN:
            if dose.is_active:
                new_state = DoseState.TAKEN
            elif (
                dose.state == DoseState.MISSED
                and now < dose.scheduled_at + DOSE_RETENTION
            ):
                new_state = DoseState.TAKEN_LATE
            elif dose.state == DoseState.SCHEDULED and self._in_early_window(dose, now):
                new_state = DoseState.TAKEN
            else:
                return False
            self._transition(med, dose, new_state, now, source=source, user_id=user_id)
            dose.next_action_at = dose.scheduled_at + DOSE_RETENTION
            self._decrement_stock(med)
            await self._notifier.async_clear(dose_tag(dose.dose_id))
            return True

        if not dose.is_active:
            return False

        if verb == ActionVerb.SKIP:
            self._transition(
                med, dose, DoseState.SKIPPED, now, source=source, user_id=user_id
            )
            dose.next_action_at = dose.scheduled_at + DOSE_RETENTION
            await self._notifier.async_clear(dose_tag(dose.dose_id))
            return True

        # Snooze
        if dose.snoozes_used >= self.settings.max_snoozes:
            _LOGGER.info("No snoozes left for dose %s", dose.dose_id)
            return False
        dose.snoozes_used += 1
        self._transition(
            med, dose, DoseState.SNOOZED, now, source=source, user_id=user_id
        )
        dose.next_action_at = now + timedelta(
            minutes=snooze_minutes or self.settings.snooze_length
        )
        return True

    def _is_finished(self, dose_id: str) -> bool:
        """Dose already taken/skipped, or no longer known at all."""
        dose = self.doses.get(dose_id)
        return dose is None or dose.state in (*TAKEN_STATES, DoseState.SKIPPED)

    def _in_early_window(self, dose: Dose, now: datetime) -> bool:
        window = timedelta(minutes=self.settings.early_take_window)
        return now < dose.scheduled_at <= now + window

    def find_dose(self, subentry_id: str, *, early: bool) -> Dose | None:
        """The dose a button acts on: the running one, else (if `early`) the
        next one inside the early-take window."""
        now = dt_util.utcnow()
        doses = sorted(
            (d for d in self.doses.values() if d.subentry_id == subentry_id),
            key=lambda dose: dose.scheduled_at,
        )
        for dose in doses:
            if dose.is_active:
                return dose
        if early:
            for dose in doses:
                if dose.state == DoseState.SCHEDULED and self._in_early_window(
                    dose, now
                ):
                    return dose
        return None

    async def async_take(
        self, subentry_id: str, *, user_id: str | None, source: Source = Source.BUTTON
    ) -> bool:
        """Take button: the running dose, or an upcoming one in the early window."""
        dose = self.find_dose(subentry_id, early=True)
        if dose is None:
            raise self._nothing_to_do("nothing_to_take", subentry_id)
        return await self.async_handle_action(
            ActionVerb.TAKEN, dose.dose_id, user_id=user_id, source=source
        )

    async def async_skip(
        self, subentry_id: str, *, user_id: str | None, source: Source = Source.BUTTON
    ) -> bool:
        """Skip button: the running dose."""
        dose = self.find_dose(subentry_id, early=False)
        if dose is None:
            raise self._nothing_to_do("nothing_to_skip", subentry_id)
        return await self.async_handle_action(
            ActionVerb.SKIP, dose.dose_id, user_id=user_id, source=source
        )

    async def async_snooze(
        self, subentry_id: str, *, user_id: str | None, source: Source = Source.BUTTON
    ) -> bool:
        """Snooze button: the running dose."""
        dose = self.find_dose(subentry_id, early=False)
        if dose is None:
            raise self._nothing_to_do("nothing_to_snooze", subentry_id)
        if dose.snoozes_used >= self.settings.max_snoozes:
            raise self._nothing_to_do("no_snoozes_left", subentry_id)
        return await self.async_handle_action(
            ActionVerb.SNOOZE, dose.dose_id, user_id=user_id, source=source
        )

    def _nothing_to_do(self, key: str, subentry_id: str) -> ServiceValidationError:
        """User-visible error for a button press that cannot do anything."""
        return ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key=key,
            translation_placeholders={"medication": self.medications[subentry_id].name},
        )

    # --- Stock --------------------------------------------------------------------

    def _decrement_stock(self, med: Medication) -> None:
        state = self.stock_state.get(med.subentry_id)
        if state is None or state.stock is None:
            return  # stock tracking off
        new_stock = state.stock - med.dose_amount
        if new_stock < 0:
            _LOGGER.warning(
                "Stock of %s would go below 0; set to 0. Check the stock value",
                med.name,
            )
            new_stock = 0.0
        state.stock = new_stock

    # --- Values read by entities -----------------------------------------------------

    @callback
    def async_notify_entities(self) -> None:
        """Tell entities to refresh their state."""
        async_dispatcher_send(self.hass, signal_update(self.entry.entry_id))

    def stock(self, subentry_id: str) -> float | None:
        """Current stock in the medication's unit; None when not tracked."""
        state = self.stock_state.get(subentry_id)
        return None if state is None else state.stock

    def days_remaining(self, subentry_id: str) -> float | None:
        """Days until stock runs out at the scheduled use (Phase 3)."""
        return None

    def next_dose(self, subentry_id: str) -> datetime | None:
        """Time of the next dose that has not started yet."""
        upcoming = [
            dose.scheduled_at
            for dose in self.doses.values()
            if dose.subentry_id == subentry_id and dose.state == DoseState.SCHEDULED
        ]
        return min(upcoming, default=None)

    def _current_dose(self, subentry_id: str) -> Dose | None:
        """The running dose, else the dose resolved most recently today."""
        doses = [d for d in self.doses.values() if d.subentry_id == subentry_id]
        active = [d for d in doses if d.is_active]
        if active:
            return min(active, key=lambda dose: dose.scheduled_at)
        start, end = local_day_bounds(dt_util.utcnow(), self.tz)
        resolved_today = [
            d
            for d in doses
            if d.resolved_at is not None and start <= d.resolved_at < end
        ]
        return max(resolved_today, key=lambda dose: dose.resolved_at, default=None)

    def status(self, subentry_id: str) -> str:
        """State of the most recent dose, or idle."""
        dose = self._current_dose(subentry_id)
        return STATUS_IDLE if dose is None else dose.state.value

    def status_attributes(self, subentry_id: str) -> dict[str, Any]:
        """Details of the current dose."""
        dose = self._current_dose(subentry_id)
        if dose is None:
            return {"dose_id": None, "reminders_sent": 0, "snoozes_used": 0}
        return {
            "dose_id": dose.dose_id,
            "reminders_sent": dose.reminders_sent,
            "snoozes_used": dose.snoozes_used,
        }

    def taken_today(self, subentry_id: str) -> int:
        """Doses of this medication scheduled today and taken."""
        start, end = local_day_bounds(dt_util.utcnow(), self.tz)
        return sum(
            1
            for dose in self.doses.values()
            if dose.subentry_id == subentry_id
            and dose.state in TAKEN_STATES
            and start <= dose.scheduled_at < end
        )

    def due_today(self, subentry_id: str) -> int:
        """Doses of this medication scheduled today."""
        start, end = local_day_bounds(dt_util.utcnow(), self.tz)
        return len(doses_between(self.medications[subentry_id], start, end, self.tz))

    def low_stock(self, subentry_id: str) -> bool:
        """Whether stock is at or below the threshold."""
        threshold = self.medications[subentry_id].low_stock_threshold
        stock = self.stock(subentry_id)
        return stock is not None and threshold is not None and stock <= threshold

    def hub_doses_due_today(self) -> int:
        """Doses of all medications scheduled today."""
        return sum(self.due_today(sid) for sid in self.medications)

    def hub_doses_taken_today(self) -> int:
        """Doses of all medications taken today."""
        return sum(self.taken_today(sid) for sid in self.medications)
