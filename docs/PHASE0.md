# Phase 0 — Preparation notes

Date: 2026-11-05

## 1. Development environment

| Item | Result |
|---|---|
| WSL2 | Ubuntu 26.04.1 LTS, WSL version 2, running |
| uv | 0.13.7 at `~/.local/bin/uv` |
| Python | CPython 3.14.10 (installed by uv; HA requires `>=3.14.2`) |
| Venv | `~/venvs/medtrack` (inside WSL home, not on /mnt/d) |
| Target HA | **2026.10.4** |
| Test plugin | `pytest-homeassistant-custom-component==0.13.375` (pins `homeassistant==2026.10.4`) |
| ruff | 0.17.3 |

HA 2026.11.0 was released today, but no matching `pytest-homeassistant-custom-component`
exists yet (the newest, 0.13.375, pins 2026.10.4). We develop and test against 2026.10.4;
bumping later is a one-line change in `requirements_test.txt`.

Run commands with:

```
wsl -e bash -lc "cd /mnt/d/Claude/dev/ha-medtrack && source ~/venvs/medtrack/bin/activate && pytest tests -q"
```

## 2. VERIFY items (checked against the HA 2026.10.4 source in the venv)

### 2.1 Config subentries (spec §3)
- `ConfigFlow.async_get_supported_subentry_types(cls, config_entry)` is a `@classmethod @callback`
  returning `dict[str, type[ConfigSubentryFlow]]`, e.g. `{"medication": MedicationSubentryFlow}`.
- `ConfigSubentryFlow` steps: `async_step_user` (add) and `async_step_reconfigure` (edit).
  In reconfigure, `self._get_reconfigure_subentry()` returns the subentry and `self._get_entry()`
  returns the hub entry.
- Finish add with `self.async_create_entry(title=..., data=..., unique_id=...)`. HA raises if a
  subentry `unique_id` is duplicated within the entry, but we also validate the name ourselves so
  the user gets a form error instead of an exception.
- Finish reconfigure with `self.async_update_and_abort(entry, subentry, title=..., data=...)`.
  **Important:** `async_update_reload_and_abort` raises `ValueError` when the entry has an update
  listener. Adding, updating and removing a subentry all fire the entry's update listeners, so the
  spec's approach (update listener → reload) works, and we use the non-reload variant in flows.
- Strings live under `config_subentries.medication` in `strings.json`, with `entry_type`,
  `initiate_flow.user` / `initiate_flow.reconfigure`, `step`, `error`, `abort`.
- Entities attach to a subentry via `async_add_entities(entities, config_subentry_id=subentry_id)`
  (one call per subentry). The device created from the entities' `device_info` is then linked to
  that subentry automatically. Removing a subentry clears its devices/entities automatically.
- Forms: `data_entry_flow.section(schema, {"collapsed": True})` is available for the optional
  "Overrides" group. `TextSelector(type=TIME, multiple=True)` gives a list of time pickers for
  "Times" (`TimeSelector` has no `multiple`).

### 2.2 Entity naming (spec §8)
- `_attr_has_entity_name = True` + `_attr_translation_key = "stock"`; name comes from
  `entity.<platform>.<key>.name` in `strings.json`. Enum states for `status` go in
  `entity.sensor.status.state.<value>`. Icons per key go in `icons.json` (`entity.<platform>.<key>.default`).
  Unchanged from previous releases.

### 2.3 Response services (spec §9)
- `hass.services.async_register(DOMAIN, "get_history", handler, schema=..., supports_response=SupportsResponse.ONLY)`;
  handler returns a dict (`ServiceResponse`). Unchanged. Per HA guidance, services are registered
  in `async_setup`, not per entry.

### 2.4 Notifications (spec §2, §6) — new finding
- HA 2026 mobile_app also creates **notify entities** (`notify.send_message`), but they only accept
  `message` and `title` — no `data`, so no actions, tags or interruption levels. We must keep using
  the legacy `notify.mobile_app_<device>` services, as the spec says.
- Those legacy services are registered asynchronously by mobile_app (discovery + reload per phone),
  so they may not exist yet when MedTrack starts. We resolve them at send time and treat a missing
  service like a failed target (warning, continue with the others).

### 2.5 `dependencies` vs `after_dependencies` (spec §13)
- Recommendation: keep `"dependencies": ["mobile_app"]`. MedTrack cannot work without it, it is part
  of `default_config` anyway, and it guarantees mobile_app is set up first. `after_dependencies` is
  for optional integrations. We will not import mobile_app Python code (we use the event name and
  service names as strings), so no further manifest entries are needed.

### 2.6 Brand icon (spec §13)
- HA 2026.10 has a `brands` integration that serves images for custom integrations **from the
  integration folder itself**: `custom_components/medtrack/brand/icon.png` (plus optional
  `icon@2x.png`, `logo.png`, `dark_icon.png`, …; missing ones fall back to `icon.png`).
  So no PR to the `home-assistant/brands` repo is needed. Sizes per brands guidelines: icon
  256×256, icon@2x 512×512, PNG with transparency. Whether the HACS validation action accepts the
  local `brand/` folder (or needs `ignore: brands`) will be checked in Phase 4.

### 2.7 Other APIs used (not marked VERIFY, checked anyway)
- `async_track_point_in_utc_time`, `async_call_later`, `async_track_time_change` exist unchanged.
- `Store(hass, version, key, minor_version=...)`; migration by subclassing and overriding
  `_async_migrate_func(old_major, old_minor, old_data)`. `async_delay_save` for routine writes.
- Tests: `freezer` fixture and `async_fire_time_changed` available in the test plugin.

## 3. Questions and ambiguities in the spec

Each item has a proposed default (→). Answers to be recorded here.

**Need an answer from the owner**
1. GitHub repo URL / username for `codeowners`, `documentation`, `issue_tracker` (git user is `s-greg`).
2. HA version running on the Proxmox VM (sets `hacs.json` minimum). → `2026.10.0`.
3. Critical alerts: the Companion docs show critical sound via `push.sound: {name: default, critical: 1, volume: 1.0}`; §6 only sets `interruption-level: critical`. → add the `sound` block for critical sends only (a small deviation from "exactly as §6").
4. Diagnostics: notify target names are usually personal ("Alex's iPhone") and instructions may be health info. → redact target names and user IDs; keep medication names, doses and instructions.
5. How long may "Taken late" be used after a dose is missed? → until 24 h after the scheduled time.

**Proposed defaults (say if you disagree)**
6. Overlap: if a dose is still pending when the same medication's next dose starts → the older one becomes `missed` (keeps one active dose per medication; buttons are unambiguous).
7. `status` sensor: shows the latest dose's state; returns to `idle` at local midnight if no dose is active.
8. Take button / `take_dose` without `dose_id`: acts on pending/snoozed dose first, then an upcoming one in the early-take window. A missed dose is only taken late via the notification or `take_dose` with `dose_id`.
9. "Missed" notification: same tag (replaces the last reminder), hub interruption level (not critical), only a "Taken late" action using the normal `MEDTRACK_TAKEN_<dose_id>` identifier.
10. `critical_mode: final_reminder` = reminder number `max_reminders` only.
11. Snooze when no snoozes are left: button logs at info; `snooze_dose` raises a user-visible error.
12. Restart recovery: a resumed dose keeps its reminder count and continues from "now" (next reminder immediately, then the normal interval). Doses whose time passed during downtime are created and handled the same way; if HA was down longer than 48 h, only the last 48 h are reconstructed.
13. `undo_last` on taken/skipped: stock restored, dose goes back to `pending` (reminders resume) if still inside its reminder window, otherwise `missed`. Undo of a refill removes the refilled amount. `set_stock` is not undoable.
14. Reconfiguring/removing a medication: a dose already in progress keeps running until resolved (removed medication: its notifications are cleared and in-flight doses dropped). History is kept.
15. Low-stock "Refilled" action is omitted when there is no default refill amount. The "already alerted" flag is persisted so a restart does not re-alert.
16. Paused medication: no doses, `days_remaining` = unknown, `next_dose` = unknown.
17. Multiple hub entries allowed (e.g. one per person, each with its own phones).
18. Hub device with `doses_due_today` / `doses_taken_today` is included.
19. A renamed phone changes its `notify.mobile_app_*` name and breaks that target → show a Repairs issue and a warning; user fixes it in the options flow.
20. Changing a medication's unit later makes HA's long-term statistics warn about a unit change; documented in the README, no code workaround.
21. Rate-budget warning (§6) is also shown as a Repairs issue, not just a log line, so the owner actually sees it.

## 4. Proposed file structure

```
custom_components/medtrack/
  __init__.py        setup/unload, update listener → reload, register services (async_setup)
  manifest.json
  const.py           domain, config keys, defaults, event names, states
  config_flow.py     hub flow, options flow, medication subentry flow (+ reconfigure)
  models.py          dataclasses: MedicationConfig, Dose, HistoryRecord (pure Python)
  schedule.py        pure functions: next dose times, DST handling, dose IDs (easy to unit-test)
  notify.py          payload builders (pure) + send/clear to all targets
  store.py           Store subclass, schema v1 + migration, history retention
  coordinator.py     MedTrackManager on entry.runtime_data: timers, state machine,
                     action-event listener, inventory, listeners for entities
  entity.py          base entity (device info per medication, update signal)
  sensor.py  binary_sensor.py  button.py
  services.py        service handlers and target resolution
  services.yaml  strings.json  translations/en.json  icons.json
  diagnostics.py
  brand/icon.png     (Phase 4)
tests/
  conftest.py        fixtures: hub entry, mocked notify services capturing payloads
  test_config_flow.py  test_schedule.py  test_notifications.py  test_dose_flow.py
  test_restart.py  test_inventory.py  test_services.py  test_diagnostics.py
pyproject.toml       ruff + pytest config
requirements_test.txt
hacs.json  README.md  LICENSE  .github/workflows/validate.yaml
```

Splitting `schedule.py` / `notify.py` / `models.py` out of the coordinator keeps the hardest logic
(times, DST, payloads) in small pure functions that can be tested without starting HA.

## 5. Order of work
Follows PROMPT.md phases 1–4. Within Phase 2: schedule.py (+ DST tests) → payload builders
(+ exact-payload tests) → state machine in coordinator → action-event handling → persistence and
startup recovery → remaining Phase 2 tests.
