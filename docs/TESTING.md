# Manual test checklist (iPhone / iPad)

Run through this after installing a new version, ideally on a test Home
Assistant first. Use a test medication with times a few minutes ahead.

## Reminders
- [ ] Reminder arrives at the set time on every selected iPhone and iPad
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

## Dashboard card (MedTrack Today)
- [ ] "MedTrack Today" appears in the card picker; the editor lets you choose the hub and shows the card version
- [ ] Ring shows "taken / total"; a low-stock banner appears at or below the threshold
- [ ] Banner → refill dialog pre-filled with the default amount; changing it and Refill adds that amount
- [ ] Running dose: "reminder n of max", Taken / Snooze / Skip; Snooze disappears when no snoozes are left
- [ ] Upcoming dose: tap shows "Take early" / "Skip" inside the early-take window, otherwise when that window opens
- [ ] Missed dose: "Taken late" button; afterwards the dose has an amber check (on-time doses keep the normal check)
- [ ] Refill dialog label uses the plural unit, e.g. "Amount (tablets)"
- [ ] After an HA restart, wait until it has fully started, open the app: the card loads (no "Custom element doesn't exist")
- [ ] Undo only on the latest taken / skipped / refilled item; the confirmation names the action and the stock change
- [ ] An action that is not possible shows a message at the bottom of the screen
- [ ] Acting from a notification or the device page updates the card within a second or two
- [ ] Light and dark theme both readable; works on the iPhone without sideways scrolling
