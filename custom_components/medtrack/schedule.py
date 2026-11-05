"""Pure scheduling functions: dose times, dose IDs and DST handling.

Nothing here touches Home Assistant state, so it can be tested directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo

from .const import WEEKDAYS
from .models import Medication

# A DST gap is at most a couple of hours anywhere; search this far for the
# next valid minute.
_MAX_GAP_MINUTES = 180


@dataclass(frozen=True, slots=True)
class ScheduledDose:
    """One dose occurrence produced by the schedule."""

    dose_id: str
    subentry_id: str
    local_date: date
    local_time: str  # nominal "HH:MM" from the configuration
    scheduled_at: datetime  # actual firing moment, UTC


def make_dose_id(subentry_id: str, local_date: date, hhmm: str) -> str:
    """Dose ID: <subentry_id>_<YYYYMMDD>_<HHMM> (nominal local date/time)."""
    return f"{subentry_id}_{local_date:%Y%m%d}_{hhmm.replace(':', '')}"


def subentry_id_from_dose_id(dose_id: str) -> str:
    """Return the subentry part of a dose ID (subentry IDs are ULIDs, no '_')."""
    return dose_id.split("_", 1)[0]


def _exists(naive: datetime, tz: tzinfo) -> bool:
    """Whether a local wall-clock time exists (is not inside a DST gap)."""
    aware = naive.replace(tzinfo=tz)
    round_trip = aware.astimezone(UTC).astimezone(tz).replace(tzinfo=None)
    return round_trip == naive


def local_to_utc(local_date: date, hhmm: str, tz: tzinfo) -> datetime:
    """Convert a nominal local date and HH:MM to the UTC moment it fires.

    - A time inside a DST gap (spring forward) fires at the next valid minute.
    - A repeated time (fall back) fires once, at its first occurrence (fold=0).
    """
    hour, minute = (int(part) for part in hhmm.split(":"))
    naive = datetime.combine(local_date, time(hour, minute))
    for _ in range(_MAX_GAP_MINUTES):
        if _exists(naive, tz):
            return naive.replace(tzinfo=tz, fold=0).astimezone(UTC)
        naive += timedelta(minutes=1)
    # Not reachable for real time zones; fail loudly rather than mis-schedule.
    raise ValueError(f"No valid local time near {local_date} {hhmm}")


def doses_between(
    medication: Medication, start: datetime, end: datetime, tz: tzinfo
) -> list[ScheduledDose]:
    """All doses of a medication with start <= scheduled_at < end (UTC times).

    Paused medications have no doses.
    """
    if not medication.active:
        return []
    doses: list[ScheduledDose] = []
    # One day of margin on both sides covers time-zone offsets.
    day = start.astimezone(tz).date() - timedelta(days=1)
    last_day = end.astimezone(tz).date() + timedelta(days=1)
    while day <= last_day:
        if WEEKDAYS[day.weekday()] in medication.days:
            for hhmm in medication.times:
                scheduled_at = local_to_utc(day, hhmm, tz)
                if start <= scheduled_at < end:
                    doses.append(
                        ScheduledDose(
                            dose_id=make_dose_id(medication.subentry_id, day, hhmm),
                            subentry_id=medication.subentry_id,
                            local_date=day,
                            local_time=hhmm,
                            scheduled_at=scheduled_at,
                        )
                    )
        day += timedelta(days=1)
    doses.sort(key=lambda dose: dose.scheduled_at)
    return doses


def local_day_bounds(moment: datetime, tz: tzinfo) -> tuple[datetime, datetime]:
    """UTC start and end of the local day containing `moment`."""
    local_date = moment.astimezone(tz).date()
    start = datetime.combine(local_date, time(0, 0), tzinfo=tz)
    end = datetime.combine(local_date + timedelta(days=1), time(0, 0), tzinfo=tz)
    return start.astimezone(UTC), end.astimezone(UTC)


def daily_push_budget(
    medications: list[Medication], max_reminders: int, max_snoozes: int
) -> int:
    """Worst-case pushes per phone per day (spec §6 rate budget).

    Per dose: every reminder, a re-send after each snooze, and the missed alert.
    """
    total = 0
    for med in medications:
        if not med.active:
            continue
        reminders = med.max_reminders or max_reminders
        total += len(med.times) * (reminders + max_snoozes + 1)
    return total
