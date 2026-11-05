# Kickoff prompt — paste this into Claude Code

```
I want to build MedTrack, a Home Assistant custom integration for medication reminders and stock tracking, installable through HACS. Read CLAUDE.md and docs/SPEC.md completely before doing anything else. The spec is the source of truth.

Work in these phases. After each phase: run ruff and the tests, commit, give me a short plain-language summary plus what I should test myself, then STOP and wait for my go-ahead.

Phase 0 – Preparation (no code yet)
- Check that WSL2 is available and set up the Python environment inside it (Python version matching the current Home Assistant release).
- Look up every item marked VERIFY in the spec against the current Home Assistant developer docs or core source, and report what you found.
- List any questions, ambiguities or problems you see in the spec.
- Propose the file structure and the order of work.

Phase 1 – Scaffold and configuration
- Repository skeleton (spec section 13), manifest, constants.
- Hub config flow, options flow, medication subentry flow with reconfigure (spec section 4), with validation and translations.
- Devices per medication, entities from spec section 8 created with placeholder values.
- Tests for the flows.

Phase 2 – Scheduler, notifications and actions
- Dose scheduling and the dose state machine (spec sections 5, 11).
- Notification payloads exactly as in spec section 6, sent to all targets; action-event handling; clearing on all phones.
- Basic stock decrement on Taken (spec section 7), so the Taken flow is complete.
- Persistence of in-flight doses and startup recovery (spec section 10).
- Tests 2, 3, 4, 5, 6, 8 and 9 from spec section 12.

Phase 3 – Inventory, history and actions
- Days remaining, stock clamping, low-stock alerts (spec sections 7 and 6).
- History store, retention, diagnostics.
- All actions from spec section 9, including get_history with a response.
- Remaining tests from spec section 12.

Phase 4 – Packaging
- hacs.json, README (including the caveats listed in spec section 13), LICENSE (MIT), GitHub Actions workflow with hassfest and HACS validation, brand icon as currently required.
- Instructions for me to install it on my Home Assistant as a HACS custom repository, and a manual test checklist for my iPhone.

Do not start the dashboard card (spec section 14); that comes later.
```
