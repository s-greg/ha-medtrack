# Changelog

## 1.2.0 (2026-11-15)

- New **Remind me at home** (requested in issue #1): link the hub to a
  person under Configure. While that person is away, reminders get a
  "Remind me at home" button; tapping it clears the reminder on all phones
  and holds the dose (no repeats, not marked missed) until the person is
  home, then the reminder comes back with the normal buttons. A safety net,
  "Remind anyway after" (default 4 hours), reminds you even if you are not
  home yet. Needs the Companion App's location permission set to Always.
- `medtrack_dose_snoozed` and its history record now include `until` (the
  time the reminder comes back, or `home`).
- Card: while the person is away a running dose also has a "Remind at home"
  button; a dose waiting until you're home shows that, with Taken and Skip.
  A dose from yesterday that is still running stays on the card after
  midnight.
- New `medtrack.remind_at_home` action (used by the card, also usable in
  automations).
- Fixed: the card could show "Custom element doesn't exist:
  medtrack-today-card", mostly in the Companion App. When the card file came
  from the browser cache it registered itself before the dashboard was
  ready; it now waits for the dashboard.

## 1.1.0 (2026-11-08)

- New **MedTrack Today** dashboard card: today's doses as a timeline with a
  progress ring, Taken / Snooze / Skip, Take early, Taken late, Undo (with
  confirmation), a Refill dialog and a low-stock banner. It ships with the
  integration and loads automatically; no dashboard resource is needed.
  Doses taken late are marked with an amber check.
- New `medtrack.get_day` action that returns today's doses, refills and
  stock (used by the card, also usable in automations and scripts).
- Ready for Home Assistant 2026.11: tested on 2026.10 and 2026.11. The hub
  device is now registered in the form that 2026.11 recommends.
- From Home Assistant 2026.11, MedTrack is installed and updated through
  **Settings → Marketplace** (custom repository; updates need the
  Marketplace's GitHub connection). Existing HACS installs move over
  automatically.

## 1.0.0 (2026-11-06)

First release.

- Medication reminders on iPhone and iPad (iOS / iPadOS) through the Home
  Assistant Companion App, with Taken, Snooze and Skip buttons, sent to all
  selected devices and cleared everywhere once answered.
- Repeating reminders, then "missed" with a "Taken late" option; optional
  critical alerts; early take and early skip before the dose time.
- Stock tracking with days remaining, a once-per-crossing low-stock alert
  with a "Refilled" button, and Refill amount / Refill on the device page.
- Undo of the last take, skip or refill (button, sensor and action).
- Permanent dose history with retention setting, `get_history` action and
  diagnostics (phone names and user IDs redacted).
- Restart recovery without bursts of old reminders.
- Configuration entirely in the UI; changes apply without reloading.
