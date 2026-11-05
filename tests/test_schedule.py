"""Tests for the pure scheduling functions (spec §11, test 2 DST part)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from custom_components.medtrack.const import WEEKDAYS, CriticalMode
from custom_components.medtrack.models import Medication
from custom_components.medtrack.schedule import (
    daily_push_budget,
    doses_between,
    local_to_utc,
    make_dose_id,
    subentry_id_from_dose_id,
)

TZ = ZoneInfo("Europe/Amsterdam")
SID = "01JABCDEFGHJKMNPQRSTVWXYZ0"


def _med(times: tuple[str, ...], days: tuple[str, ...] = WEEKDAYS, **kw) -> Medication:
    values = {
        "subentry_id": SID,
        "name": "Test",
        "dose_amount": 1.0,
        "unit": "tablet",
        "times": times,
        "days": days,
        "instructions": None,
        "initial_stock": None,
        "low_stock_threshold": None,
        "refill_amount": None,
        "critical_mode": CriticalMode.NEVER,
        "active": True,
        "reremind_interval": None,
        "max_reminders": None,
    }
    values.update(kw)
    return Medication(**values)


def test_dose_id_format() -> None:
    """IDs use the nominal local date and time."""
    dose_id = make_dose_id(SID, date(2026, 11, 5), "08:00")
    assert dose_id == f"{SID}_20261105_0800"
    assert subentry_id_from_dose_id(dose_id) == SID


def test_normal_local_time() -> None:
    """08:00 in Amsterdam in November (CET, UTC+1) is 07:00 UTC."""
    assert local_to_utc(date(2026, 11, 5), "08:00", TZ) == datetime(
        2026, 11, 5, 7, 0, tzinfo=UTC
    )


def test_spring_forward_fires_at_next_valid_minute() -> None:
    """02:30 does not exist on 29 March 2026; it fires at 03:00 CEST."""
    fired = local_to_utc(date(2026, 3, 29), "02:30", TZ)
    assert fired == datetime(2026, 3, 29, 1, 0, tzinfo=UTC)
    assert fired.astimezone(TZ).strftime("%H:%M") == "03:00"


def test_fall_back_fires_once_at_first_occurrence() -> None:
    """02:30 happens twice on 25 October 2026; it fires once, the first time."""
    fired = local_to_utc(date(2026, 10, 25), "02:30", TZ)
    assert fired == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)  # 02:30 CEST

    doses = doses_between(
        _med(("02:30",)),
        datetime(2026, 10, 24, 22, 0, tzinfo=UTC),
        datetime(2026, 10, 25, 22, 0, tzinfo=UTC),
        TZ,
    )
    assert [d.scheduled_at for d in doses] == [fired]


def test_doses_between_respects_days_and_order() -> None:
    """Only selected weekdays, sorted by time, end exclusive."""
    med = _med(("20:00", "08:00"), days=("thu",))  # 2026-11-05 is a Thursday
    doses = doses_between(
        med,
        datetime(2026, 11, 2, 0, 0, tzinfo=UTC),
        datetime(2026, 11, 9, 0, 0, tzinfo=UTC),
        TZ,
    )
    assert [d.dose_id for d in doses] == [
        f"{SID}_20261105_0800",
        f"{SID}_20261105_2000",
    ]
    assert [d.local_time for d in doses] == ["08:00", "20:00"]


def test_paused_medication_has_no_doses() -> None:
    """Inactive medications produce nothing."""
    doses = doses_between(
        _med(("08:00",), active=False),
        datetime(2026, 11, 5, 0, 0, tzinfo=UTC),
        datetime(2026, 11, 7, 0, 0, tzinfo=UTC),
        TZ,
    )
    assert doses == []


def test_daily_push_budget() -> None:
    """doses per day x (max reminders + max snoozes + 1), per-med override honoured."""
    meds = [_med(("08:00", "20:00")), _med(("12:00",), max_reminders=10)]
    assert daily_push_budget(meds, max_reminders=4, max_snoozes=3) == 2 * 8 + 1 * 14
