# MedTrack — Home Assistant medication reminder & inventory integration

Specification v1.0 · Target: Home Assistant custom integration, installable via HACS (custom repository)

Items marked **VERIFY** depend on Home Assistant APIs that change between releases. Check them against the current developer docs (https://developers.home-assistant.io) and the HA core source of the installed version before implementing. Do not guess.

---

## 1. Goal

A self-hosted, Medisafe-like medication tracker that runs entirely inside Home Assistant:

- Sends reminders to one or more iPhones through the official Home Assistant Companion App, with action buttons (Taken / Snooze / Skip).
- Repeats the reminder until the dose is confirmed, or until a configurable maximum is reached, after which the dose is marked **missed**.
- Tracks stock per medication and alerts when stock falls to or below a threshold.
- Keeps a permanent dose history that survives HA's recorder purge (default 10 days).
- Exposes entities and actions (services) so dashboards and automations can use the data.

Non-goals for v1: Android-specific features, drug-interaction data, multiple patients/profiles per entry, cloud sync, a custom dashboard card (that is Phase 6).

## 2. Platform facts this design relies on

These were checked against the Companion App docs (companion.home-assistant.io) in November 2026.

- Notifications are sent with `notify.mobile_app_<device>` actions. Inline `actions` (array of `{action, title}`) are supported on iOS; categories are deprecated. iOS shows up to ~10 actions; use 3.
- When a button is tapped, HA fires the event `mobile_app_notification_action` with `data.action` = the action identifier. On iOS, `data.action_data` echoes back whatever was sent as `action_data` in the notification. `context.user_id` identifies the HA user of the app.
- Action identifiers are shared across all notifications, so every identifier must be unique per dose.
- `tag` replaces an existing notification with the same tag. Sending `message: clear_notification` with `data.tag` removes it from the device.
- `data.group` threads notifications together on iOS.
- iOS interruption levels via `data.push.interruption-level`: `passive`, `active`, `time-sensitive`, `critical`. Critical alerts play sound even when the phone is muted or in Do Not Disturb. The HA app already has Apple's critical-alert entitlement.
- iOS Action option `authenticationRequired: true` forces the phone to be unlocked before the action runs; `destructive: true` colors it red.
- The iOS app automatically adds its own *local* snooze actions to every notification. These are invisible to HA. The integration's own "Snooze" button is the one that matters; document this for the user.
- Rate limit: 500 push notifications per device per day (resets midnight UTC). Critical alerts and notification commands (like `clear_notification`) do not count.
- Tapping an action requires the phone to reach HA (Nabu Casa, reverse proxy, or VPN). If it cannot, the tap is lost and re-reminders continue. This is expected behavior.
- Native Windows cannot run HA core or `pytest-homeassistant-custom-component` (POSIX-only modules). Development and tests run in WSL2 or a devcontainer.

## 3. Architecture

- **Domain:** `medtrack` · **Name:** MedTrack · `integration_type: hub` · `iot_class: calculated` · `config_flow: true`
- **One config entry** (the "hub") holds global settings.
- **One config subentry of type `medication`** per medication (config subentries, available since HA 2025). Each medication gets its own HA **device**, and its entities are attached to the subentry. **VERIFY:** current subentry flow API (`ConfigSubentryFlow`, `async_get_supported_subentry_types`, reconfigure step naming) and how entities are associated with a subentry (`async_add_entities(..., config_subentry_id=...)`).
- **Coordinator / manager object** per config entry (stored on `entry.runtime_data`) owns: the schedule, dose state machine, notification sending, action-event listener, inventory, and history store.
- **Persistence:** `homeassistant.helpers.storage.Store`, key `medtrack.<entry_id>`, with a schema version and migration function. Use delayed saves for routine writes and immediate saves for state transitions.
- **No polling.** Use `async_track_point_in_time` (or `async_track_point_in_utc_time`) for each scheduled event; reschedule on startup, on subentry change, and at local midnight for the next day.
- When a subentry is added, changed or removed: reload the config entry (update listener) so schedules and entities rebuild cleanly.

## 4. Configuration

### 4.1 Hub (config flow `user` step + options flow)

| Field | Type | Default | Notes |
|---|---|---|---|
| Name | text | "MedTrack" | Entry title |
| Notify targets | multi-select | — (required, ≥1) | Built from the available `notify.mobile_app_*` services at flow time |
| Re-remind interval | minutes (1–120) | 10 | Default for all medications |
| Max reminders | int (1–20) | 4 | Total sends including the first |
| Max snoozes | int (0–10) | 3 | Per dose |
| Snooze length | minutes (5–120) | 15 | |
| Early-take window | minutes (0–180) | 60 | How early a dose may be confirmed before its time |
| Interruption level | select | time-sensitive | `active` / `time-sensitive` for normal reminders |
| Require unlock for "Taken" | bool | false | Sets `authenticationRequired` on the Taken action |
| Critical alert volume | float (0.1–1.0) | 1.0 | Volume in the critical `sound` block (§6) |
| History retention | days (0 = forever) | 0 | |

### 4.2 Medication (subentry flow + reconfigure)

| Field | Type | Notes |
|---|---|---|
| Name | text, required, unique within entry | Used for device and entity names |
| Dose amount | float > 0 | e.g. 2 |
| Unit | text | e.g. tablet, capsule, ml. Used for stock too |
| Times | list of HH:MM, ≥1 | Local time (HA's configured time zone) |
| Days | multi-select Mon–Sun | Default all |
| Instructions | text, optional | e.g. "with food"; shown in the notification body |
| Current stock | float ≥ 0, optional | Empty = stock tracking off for this medication (no decrement, no low-stock alerts, no Stock / Days remaining / Low stock entities). Filling it in later turns tracking on. In reconfigure the field shows the live stock; changing it is a logged stock correction (same as `set_stock`), not a reset |
| Low-stock threshold | float ≥ 0, optional | Alert when stock ≤ threshold. Empty = track stock but never alert |
| Default refill amount | float, optional | Pre-fills the refill action |
| Critical mode | select | `never` / `final_reminder` / `all_reminders` |
| Overrides | optional | Re-remind interval, max reminders (fall back to hub values) |
| Active | bool | Paused medications send nothing |

Validation: times are unique and valid; at least one day; numeric ranges as above. All UI strings in `strings.json` / `translations/en.json`.

## 5. Dose state machine

Each scheduled dose has an ID: `<subentry_id>_<YYYYMMDD>_<HHMM>` (local date/time).

States: `scheduled → pending → (snoozed ⇄ pending) → taken | taken_late | skipped | missed`

- **At dose time:** state `pending`, reminder #1 sent.
- **Every re-remind interval while pending:** send reminder #n with the **same tag** (replaces the old one instead of stacking). Stop after `max_reminders`.
- **Missed:** one interval after the last reminder with no response → `missed`. Send a final "Missed: <name>" notification that still offers a **"Taken late"** action. Fire event `medtrack_dose_missed`.
- **Snooze:** next reminder at now + snooze length; does not consume a reminder; allowed `max_snoozes` times, after which the Snooze button is omitted.
- **Taken:** `taken` (or `taken_late` if after `missed`). Decrement stock. Clear the notification on **all** targets (`clear_notification` + tag). Cancel pending timers.
- **Skipped:** `skipped`, no stock change, clear on all targets.
- **Early take:** a Taken action (button entity or service) within the early-take window before the dose time resolves that upcoming dose; no reminder is sent.
- **Idempotency:** an action for an already-resolved dose (for example, tapped on a second phone after the first) is ignored and just triggers a clear on all targets.
- **Pause / inactive medication:** no doses generated.

Each transition fires a bus event `medtrack_dose_<state>` with `{medication, dose_id, scheduled_at, at, user_id}` for automations and the logbook, and appends a history record.

## 6. Notifications

Per dose (one notification per medication dose; doses at the same minute are separate notifications in the same `group`):

```yaml
action: notify.mobile_app_<device>
data:
  title: "Medication: <name>"
  message: "<dose amount> <unit> · <HH:MM><instructions suffix> (reminder n of max)"
  data:
    tag: "medtrack_<dose_id>"
    group: "medtrack"
    push:
      interruption-level: "<time-sensitive | critical>"
    action_data:
      dose_id: "<dose_id>"
    actions:
      - action: "MEDTRACK_TAKEN_<dose_id>"
        title: "Taken"
      - action: "MEDTRACK_SNOOZE_<dose_id>"
        title: "Snooze <n> min"
      - action: "MEDTRACK_SKIP_<dose_id>"
        title: "Skip"
        destructive: true
```

- Use `interruption-level: critical` when the medication's critical mode says so (final reminder, or all reminders). Otherwise use the hub's interruption level.
- Critical sends only also include `push.sound: {name: default, critical: 1, volume: <critical alert volume>}`.
- The phone list in the hub flows shows each phone's name (from its mobile_app entry); the stored value is the notify service name.
- Optional `icon: "sfsymbols:..."` on actions (iOS 15+); keep it optional.
- Parse actions by prefix `MEDTRACK_`, then verb, then dose ID. Prefer `action_data.dose_id` when present, fall back to parsing the identifier.
- Send to all targets concurrently. If one target fails, log a warning and continue with the others; never let a notify error break the scheduler.
- Clearing: `message: clear_notification`, `data: {tag: "medtrack_<dose_id>"}` to every target.
- **Low stock:** one notification per threshold crossing (not on every dose), tag `medtrack_lowstock_<subentry_id>`, with a "Refilled" action that applies the default refill amount if set. Re-arms after stock rises above the threshold.
- **Rate budget:** worst case per device per day = Σ over meds of (doses/day × (max_reminders + max_snoozes + 1)). Log a warning at setup if this exceeds 400.

## 7. Inventory

- Applies only to medications with stock tracking on (current stock filled in, §4.2). Low-stock alerts additionally need a threshold.
- Stock is a float in the medication's unit. Taken / taken late subtracts the dose amount; never goes below 0 (clamp and log a warning).
- Days remaining = stock ÷ average daily use, where average daily use = dose × len(times) × (active days ÷ 7). Unknown (None) if usage is 0.
- Undo restores stock (see actions).

## 8. Entities (per medication device)

`has_entity_name = True`; unique IDs based on `subentry_id`. **VERIFY:** entity naming via translation keys.

| Platform | Key | Details |
|---|---|---|
| sensor | `stock` | Numeric, `state_class: measurement`, unit = medication unit (gives long-term statistics). Displayed as a whole number unless the dose amount has decimals. Only with stock tracking |
| sensor | `days_remaining` | Numeric, unit days. Only with stock tracking |
| sensor | `next_dose` | `device_class: timestamp` |
| sensor | `status` | `device_class: enum`: `idle`, `pending`, `snoozed`, `taken`, `taken_late`, `skipped`, `missed` (state of the most recent dose). Attributes: current dose ID, reminders sent, snoozes used |
| sensor | `taken_today` | Numeric count of doses taken today; attribute `due_today` |
| binary_sensor | `low_stock` | `device_class: problem`. Only with stock tracking; always off without a threshold |
| button | `take` | Confirms the pending dose, or an upcoming one within the early-take window; otherwise does nothing and logs at info level |
| button | `skip` | Skips the pending dose |
| button | `snooze` | Snoozes the pending dose |

Hub device (optional): sensor `doses_due_today`, sensor `doses_taken_today`.

## 9. Actions (services) — `services.yaml` with descriptions

All target a medication via `device_id` or `entity_id` of one of its entities.

| Action | Fields | Effect |
|---|---|---|
| `medtrack.take_dose` | `dose_id` optional | Same rules as the Taken button |
| `medtrack.skip_dose` | `dose_id` optional | |
| `medtrack.snooze_dose` | `minutes` optional | |
| `medtrack.refill` | `amount` optional (default refill amount) | Adds stock, logs a refill record |
| `medtrack.set_stock` | `amount` required | Stock correction, logged |
| `medtrack.undo_last` | — | Reverts the medication's last taken/skipped/refill (within 24 h), restores stock |
| `medtrack.get_history` | `start`, `end`, optional medication | **Returns a response** (`SupportsResponse.ONLY`) with history records; used by the dashboard card later. **VERIFY** response-service API |

## 10. History (Store)

Record: `{id, type: dose|refill|stock_set|undo, medication_id, dose_id?, scheduled_at?, state?, at, user_id?, amount?, source: notification|button|service|system|config}`. `config` marks a stock correction made in the medication form.

- Persist the in-flight state of all pending doses so a restart resumes them.
- **Startup recovery:** for doses whose time passed while HA was down, resume if still inside the reminder window (send the next reminder immediately), otherwise mark `missed` with `source: system`.
- Apply the retention setting daily.
- Schema version 1 plus a migration hook.
- Diagnostics (`diagnostics.py`) returning config and the last 50 history records, with nothing redacted except user IDs.

## 11. Time handling

- All scheduling in HA's configured time zone using `homeassistant.util.dt`. Store timestamps in UTC ISO 8601.
- DST: a local time that does not exist (spring forward) fires at the next valid minute; a repeated local time (fall back) fires once.
- Build the next 24–48 h of dose timers at a time and roll forward at local midnight.

## 12. Quality requirements

- Fully async; no blocking I/O in the event loop. Type hints, `from __future__ import annotations`, ruff-clean.
- Tests with `pytest-homeassistant-custom-component` (run in WSL2/devcontainer), using time travel (`async_fire_time_changed` / freezer). Required test cases:
  1. Config flow and subentry add / reconfigure / remove
  2. Reminder fires at the scheduled local time; DST spring-forward and fall-back cases
  3. Re-reminders stop at max, then `missed`; "Taken late" after missed
  4. Snooze shifts the next reminder; snooze limit hides the Snooze action
  5. Taken from notification decrements stock and clears all targets; duplicate action is idempotent
  6. Early take via button cancels the upcoming reminder
  7. Low-stock alert fires once per crossing and re-arms after refill
  8. Restart mid-dose resumes reminders; restart after window marks missed
  9. Notify failure on one target does not stop the others
  10. `undo_last` restores stock
- Mock the notify services in tests; assert on the exact payloads from section 6.

## 13. Packaging (HACS custom repository)

Repository root:

```
custom_components/medtrack/   (__init__.py, manifest.json, config_flow.py, const.py, coordinator.py,
                               sensor.py, binary_sensor.py, button.py, services.yaml, strings.json,
                               translations/en.json, diagnostics.py, icons.json)
tests/
hacs.json
README.md
LICENSE
.github/workflows/validate.yaml  (hassfest + HACS action)
```

- `manifest.json` must include `domain`, `name`, `version` (SemVer), `codeowners`, `documentation`, `issue_tracker`, plus `config_flow`, `integration_type`, `iot_class`, `dependencies: ["mobile_app"]` (**VERIFY** whether `after_dependencies` is more appropriate).
- `hacs.json`: `name`, minimum `homeassistant` version (set to the version you developed against).
- Brand icon: **VERIFY** current HACS/HA brand-asset requirements for custom integrations.
- README: installation via HACS custom repository, setup, the remote-access requirement for action buttons, the iOS built-in snooze caveat, the rate limit, and a "do not rely on this as your only reminder for critical medication" note.

## 14. Phase 6 (later): dashboard card

A custom Lovelace card (plain ES module web component, no build step, served from `www/` or bundled via HACS as a separate "Dashboard" repo) using the entities and `medtrack.get_history`. Three visual directions exist in `design-reference/` (static HTML mockups, 390×844 iPhone layout; read for layout, hierarchy and colors, not as runnable code). Which direction to build is decided before Phase 6 starts.
