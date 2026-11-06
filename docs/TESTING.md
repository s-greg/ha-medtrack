# Manual test checklist (iPhone)

Run through this after installing a new version, ideally on a test Home
Assistant first. Use a test medication with times a few minutes ahead.

## Reminders
- [ ] Reminder arrives at the set time on every selected iPhone
- [ ] Re-reminders replace the previous notification instead of stacking, and still play a sound
- [ ] Tapping the notification itself opens the medication's device page (or your configured page)
- [ ] "Taken" on one phone clears the notification on the other phone; stock goes down by the dose amount
- [ ] Snooze delays the next reminder by the snooze length; after the max snoozes the Snooze button is gone
- [ ] After the max number of reminders a "Missed" notification arrives, and "Taken late" works
- [ ] Critical mode sounds with the phone on silent and in a Focus mode
- [ ] Pausing a medication while a reminder is showing removes it; status becomes "skipped"

## Device page
- [ ] Take / Skip up to the early-take window before a dose: confirmed, and no reminder at dose time
- [ ] Take / Skip / Snooze with nothing to act on shows an error explaining why
- [ ] Refill amount box shows whole numbers (decimals only for decimal doses); Refill adds it and resets the box
- [ ] Without a default refill amount, an empty box + Refill shows an error
- [ ] Last action shows the latest take / skip / refill; Undo last reverts it and Last action steps back; with nothing left, Undo shows an error

## Stock
- [ ] Low-stock alert arrives once when crossing the threshold, not on later doses
- [ ] "Refilled" on the alert adds the default once (tapping it on the second phone does nothing) and removes the alert from both phones
- [ ] Clearing "Default refill amount" in the form saves (no default afterwards)

## Robustness
- [ ] Restart HA during a pending dose: reminders resume afterwards, no burst of old reminders
- [ ] Saving a medication form or the settings adds no "Unavailable" entries to the logbook
- [ ] Tap "Taken" away from home with VPN off, then on: understand what happens in both cases
- [ ] `medtrack.get_history` in Developer tools → Actions returns your recent actions
- [ ] Download diagnostics: no phone names or user IDs in the file
