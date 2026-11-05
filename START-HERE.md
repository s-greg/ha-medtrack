# Start here

## What's in this kit
- `CLAUDE.md`: project rules Claude Code reads automatically every session.
- `docs/SPEC.md`: the full specification (features, behavior, entities, tests).
- `PROMPT.md`: the prompt to paste into Claude Code to start.
- `design-reference/`: the three dashboard mockups (A, B, C) for the later dashboard card. They are static mockups for reference only.

## One-time setup on your Windows PC
1. Install WSL2 with Ubuntu. Open PowerShell as administrator, run `wsl --install -d Ubuntu`, then restart. Home Assistant's code and tests don't run on native Windows, so Claude Code will use this.
2. Install Git for Windows if you don't have it yet; Claude Code on Windows needs it.
3. Create an empty GitHub repository named `ha-medtrack`. HACS cannot install from private repositories, so make it public when you want to install through HACS. It contains only code, no personal data; your medication settings live in your own Home Assistant. While developing you can keep it private and copy files to HA through the Samba share instead.
4. Clone it to `D:\Claude\dev\ha-medtrack` (avoid OneDrive-synced folders like Documents) and copy the contents of this kit into it, keeping the folder structure.
5. Open that folder in the Claude desktop app's Code tab and paste the prompt from `PROMPT.md`.

## Recommended: a test Home Assistant
Test new versions on a second, throwaway HA instance on Proxmox (a small HA OS VM) before installing them on your main one. A bug in a custom integration can stop HA from starting. Your Companion App can connect to both servers.

## Before you rely on it
- **Remote access:** tapping "Taken" away from home only works if your iPhone can reach HA at that moment, through Nabu Casa or your WireGuard VPN set to connect on demand. Otherwise the tap is lost and the reminders keep coming.
- **iOS snooze:** iOS adds its own snooze options to every notification. HA doesn't know about those; use MedTrack's own "Snooze" button.
- **Backup alarm:** if HA, your internet or the push service is down at dose time, no reminder arrives. For medication that really matters, keep a phone alarm as a backup.

## iPhone test checklist (after Phase 4)
- [ ] Reminder arrives at the set time on every selected iPhone
- [ ] Re-reminders replace the previous notification instead of stacking, and still play a sound
- [ ] "Taken" on one phone clears the notification on the other phone
- [ ] Stock goes down by the dose amount
- [ ] Snooze delays the next reminder by the set time
- [ ] After the max number of reminders you get a "Missed" notification, and "Taken late" works
- [ ] Critical mode sounds with the phone on silent and in a Focus mode
- [ ] Low-stock alert arrives once when crossing the threshold
- [ ] Restart HA during a pending dose: reminders resume afterwards
- [ ] Tap "Taken" away from home with VPN off, then on: understand what happens in both cases
