# CLAUDE.md — MedTrack

## What this is
A Home Assistant custom integration (`custom_components/medtrack`) that sends repeating medication reminders to iPhones via the HA Companion App, tracks doses and stock, and is installable via HACS. The full specification is in `docs/SPEC.md`; it is the source of truth. If something in the spec is unclear or seems wrong, ask before deviating.

## Owner context
- The owner is not a professional Python developer. Explain decisions briefly in plain language at the end of each phase.
- Primary user devices: iPhones with the HA Companion App. No Android support needed in v1.
- HA runs on a separate machine (Proxmox VM). This repo lives on a Windows PC.

## Development environment (Windows)
- Home Assistant core and `pytest-homeassistant-custom-component` do NOT run on native Windows. Run all Python tooling inside WSL2 (Ubuntu):
  `wsl -e bash -lc "cd /mnt/d/Claude/dev/ha-medtrack && <command>"` (the repo lives at D:\Claude\dev\ha-medtrack; Windows drives are mounted under /mnt/ in WSL)
  If WSL2 is not installed, stop and tell the owner how to install it (`wsl --install -d Ubuntu`) rather than working around it.
- Use `uv` (installed in WSL by the owner) to install the Python version required by the current HA release and to create the virtual environment. Put the venv inside WSL's home directory (e.g. `~/venvs/medtrack`), not on /mnt/d, for speed.
- You cannot use `sudo` (it needs the owner's password). If a system package is missing, tell the owner the exact command to run.
- Pin `pytest-homeassistant-custom-component` to the version matching the HA release you target.

## Conventions
- Follow current HA developer docs (developers.home-assistant.io). Before using any API marked **VERIFY** in the spec, check the docs or the HA core source for the targeted version.
- Fully async; no blocking calls in the event loop. Type hints everywhere, `from __future__ import annotations`.
- ruff for lint and format.
- Config via UI only (config flow + subentries + options flow); no YAML configuration.
- All user-facing strings in `strings.json` and `translations/en.json`.
- Never log personal data beyond medication names and dose IDs.

## Workflow rules
- Work in the phases defined in `PROMPT.md`. At the end of each phase: run lint and tests, commit with a clear message, summarise what was built and what the owner should test, then STOP and wait for approval.
- Do not modify anything outside this repository. Never connect to or change the owner's live Home Assistant instance yourself; give the owner the steps instead.
- Do not add third-party Python requirements unless clearly needed; ask first.

## Commands (inside WSL)
- Tests: `pytest tests -q`
- Lint: `ruff check . && ruff format --check .`
