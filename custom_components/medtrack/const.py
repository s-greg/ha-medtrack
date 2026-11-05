"""Constants for MedTrack."""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum
from typing import Final

DOMAIN: Final = "medtrack"
DEFAULT_NAME: Final = "MedTrack"

SUBENTRY_MEDICATION: Final = "medication"

# Prefix of the legacy notify services created by the Companion App.
MOBILE_APP_NOTIFY_PREFIX: Final = "mobile_app_"

# --- Hub settings (stored in entry.options) -----------------------------------
CONF_NOTIFY_TARGETS: Final = "notify_targets"
CONF_REREMIND_INTERVAL: Final = "reremind_interval"
CONF_MAX_REMINDERS: Final = "max_reminders"
CONF_MAX_SNOOZES: Final = "max_snoozes"
CONF_SNOOZE_LENGTH: Final = "snooze_length"
CONF_EARLY_TAKE_WINDOW: Final = "early_take_window"
CONF_INTERRUPTION_LEVEL: Final = "interruption_level"
CONF_REQUIRE_UNLOCK: Final = "require_unlock"
CONF_CRITICAL_VOLUME: Final = "critical_volume"
CONF_HISTORY_RETENTION: Final = "history_retention"
# Page the Companion App opens when a reminder is tapped; empty = the
# medication's device page.
CONF_TAP_PATH: Final = "tap_path"

DEFAULT_REREMIND_INTERVAL: Final = 10
DEFAULT_MAX_REMINDERS: Final = 4
DEFAULT_MAX_SNOOZES: Final = 3
DEFAULT_SNOOZE_LENGTH: Final = 15
DEFAULT_EARLY_TAKE_WINDOW: Final = 60
DEFAULT_REQUIRE_UNLOCK: Final = False
DEFAULT_CRITICAL_VOLUME: Final = 1.0
DEFAULT_HISTORY_RETENTION: Final = 0

# (min, max) ranges, inclusive.
RANGE_REREMIND_INTERVAL: Final = (1, 120)
RANGE_MAX_REMINDERS: Final = (1, 20)
RANGE_MAX_SNOOZES: Final = (0, 10)
RANGE_SNOOZE_LENGTH: Final = (5, 120)
RANGE_EARLY_TAKE_WINDOW: Final = (0, 180)
RANGE_CRITICAL_VOLUME: Final = (0.1, 1.0)
RANGE_HISTORY_RETENTION: Final = (0, 3650)


class InterruptionLevel(StrEnum):
    """iOS interruption levels allowed for normal reminders."""

    ACTIVE = "active"
    TIME_SENSITIVE = "time-sensitive"


DEFAULT_INTERRUPTION_LEVEL: Final = InterruptionLevel.TIME_SENSITIVE

# --- Medication settings (stored in subentry.data) ------------------------------
CONF_DOSE_AMOUNT: Final = "dose_amount"
CONF_UNIT: Final = "unit"
CONF_TIMES: Final = "times"
CONF_DAYS: Final = "days"
CONF_INSTRUCTIONS: Final = "instructions"
CONF_CURRENT_STOCK: Final = "current_stock"
CONF_LOW_STOCK_THRESHOLD: Final = "low_stock_threshold"
CONF_REFILL_AMOUNT: Final = "refill_amount"
CONF_CRITICAL_MODE: Final = "critical_mode"
CONF_ACTIVE: Final = "active"
# Form section that groups the per-medication overrides. The values themselves
# are stored flat in subentry.data using CONF_REREMIND_INTERVAL / CONF_MAX_REMINDERS.
SECTION_OVERRIDES: Final = "overrides"
OVERRIDE_KEYS: Final = (CONF_REREMIND_INTERVAL, CONF_MAX_REMINDERS)

DEFAULT_UNIT: Final = "tablet"

# Weekday keys in Python's weekday() order (Monday == 0).
WEEKDAYS: Final = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class CriticalMode(StrEnum):
    """When a medication's reminders use iOS critical alerts."""

    NEVER = "never"
    FINAL_REMINDER = "final_reminder"
    ALL_REMINDERS = "all_reminders"


class DoseState(StrEnum):
    """States of a single dose."""

    SCHEDULED = "scheduled"
    PENDING = "pending"
    SNOOZED = "snoozed"
    TAKEN = "taken"
    TAKEN_LATE = "taken_late"
    SKIPPED = "skipped"
    MISSED = "missed"


ACTIVE_STATES: Final = frozenset({DoseState.PENDING, DoseState.SNOOZED})
TAKEN_STATES: Final = frozenset({DoseState.TAKEN, DoseState.TAKEN_LATE})


class Source(StrEnum):
    """Where a dose transition or stock change came from (history `source`)."""

    NOTIFICATION = "notification"
    BUTTON = "button"
    SERVICE = "service"
    SYSTEM = "system"
    CONFIG = "config"


# --- Scheduling -----------------------------------------------------------------
# How far ahead dose timers are built (spec §11: 24-48 h).
SCHEDULE_HORIZON: Final = timedelta(hours=48)
# "Taken late" is accepted until this long after the scheduled time; resolved
# doses are also kept in memory this long (for idempotent duplicate taps).
DOSE_RETENTION: Final = timedelta(hours=24)
# After a restart, at most this much downtime is reconstructed.
MAX_RECOVERY: Final = timedelta(hours=48)
# Warn when the worst-case pushes per phone per day exceed this (limit is 500).
RATE_BUDGET_WARNING: Final = 400
NOTIFY_TIMEOUT: Final = 30  # seconds per notify call

# --- Notifications ------------------------------------------------------------
# Default page opened by tapping a reminder: the medication's device page,
# where its Take / Skip / Snooze buttons are.
DEVICE_PAGE_PATH: Final = "/config/devices/device/{device_id}"
NOTIFY_GROUP: Final = "medtrack"
TAG_PREFIX: Final = "medtrack_"
ACTION_PREFIX: Final = "MEDTRACK_"
EVENT_MOBILE_APP_ACTION: Final = "mobile_app_notification_action"


class ActionVerb(StrEnum):
    """Verb part of a MEDTRACK_<VERB>_<id> action identifier."""

    TAKEN = "TAKEN"
    SNOOZE = "SNOOZE"
    SKIP = "SKIP"


# Bus events: medtrack_dose_<state>
EVENT_DOSE_PREFIX: Final = f"{DOMAIN}_dose_"

# --- Storage -------------------------------------------------------------------
STORAGE_VERSION: Final = 1
STORAGE_MINOR_VERSION: Final = 1
SAVE_DELAY: Final = 10  # seconds, for routine (non-transition) saves

# Entities that only exist while stock tracking is on: (platform, key).
STOCK_ENTITY_KEYS: Final = (
    ("sensor", "stock"),
    ("sensor", "days_remaining"),
    ("binary_sensor", "low_stock"),
)

# Values of the per-medication status sensor (state of the most recent dose).
STATUS_IDLE: Final = "idle"
STATUS_OPTIONS: Final = [
    STATUS_IDLE,
    DoseState.PENDING,
    DoseState.SNOOZED,
    DoseState.TAKEN,
    DoseState.TAKEN_LATE,
    DoseState.SKIPPED,
    DoseState.MISSED,
]
