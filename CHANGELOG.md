# Changelog

## 1.0.0 (2026-11-06)

First release.

- Medication reminders on iPhone through the Home Assistant Companion App,
  with Taken, Snooze and Skip buttons, sent to all selected phones and
  cleared everywhere once answered.
- Repeating reminders, then "missed" with a "Taken late" option; optional
  critical alerts; early take and early skip before the dose time.
- Stock tracking with days remaining, a once-per-crossing low-stock alert
  with a "Refilled" button, and Refill amount / Refill on the device page.
- Undo of the last take, skip or refill (button, sensor and action).
- Permanent dose history with retention setting, `get_history` action and
  diagnostics (phone names and user IDs redacted).
- Restart recovery without bursts of old reminders.
- Configuration entirely in the UI; changes apply without reloading.
