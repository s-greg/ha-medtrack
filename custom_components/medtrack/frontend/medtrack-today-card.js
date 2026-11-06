/**
 * MedTrack Today: today's doses of one MedTrack hub, with Taken / Snooze /
 * Skip, Take early, Taken late, Undo and Refill.
 *
 * Served and loaded by the MedTrack integration itself (no build step, no
 * dependencies). All logic lives in Python: the card renders the response of
 * the `medtrack.get_day` action and calls the normal MedTrack actions.
 * It fetches again on load, whenever a MedTrack entity changes, when the app
 * comes back to the foreground, and at the response's `refresh_at`.
 */

const DOMAIN = "medtrack";
const CARD_TYPE = "medtrack-today-card";
const VERSION = new URL(import.meta.url).searchParams.get("v") || "dev";

// English texts of the card (the integration's own texts are in strings.json).
const T = {
  today: "Today",
  loading: "Loading\u2026",
  retry: "Try again",
  ofTaken: (taken, total) => `${taken} of ${total} doses taken today`,
  noMedications: "No medications yet",
  noMedicationsHint:
    "Add one under Settings \u2192 Devices & services \u2192 MedTrack \u2192 Add medication.",
  noDoses: "No doses today",
  noDosesHint: "Nothing is scheduled for today.",
  runningLow: (name) => `${name} is running low`,
  left: (stock) => `${stock} left`,
  aboutDays: (days) => (days === 1 ? "about 1 day" : `about ${days} days`),
  taken: (time) => `taken ${time}`,
  takenLate: (time) => `taken late ${time}`,
  skipped: (time) => `skipped ${time}`,
  missed: "missed",
  upcoming: "upcoming",
  due: (n, max) => `Due \u00b7 reminder ${n} of ${max}`,
  snoozed: (time, n, max) => `Snoozed until ${time} \u00b7 reminder ${n} of ${max}`,
  takeFrom: (time) => `Can be taken or skipped from ${time}`,
  refilled: (quantity) => `Refilled +${quantity}`,
  btnTaken: "Taken",
  btnTakenLate: "Taken late",
  btnTakeEarly: "Take early",
  btnSnooze: "Snooze",
  btnSkip: "Skip",
  btnUndo: "Undo",
  btnCancel: "Cancel",
  btnRefill: "Refill",
  undoTitle: "Undo?",
  undoQuestion: (label, name) => `Undo \u201c${label}\u201d for ${name}?`,
  stockChange: (sign, quantity) => `Stock ${sign}${quantity}`,
  undoChanged: "This changed in the meantime. Check the card and try again.",
  refillTitle: (name) => `Refill ${name}`,
  refillLabel: (unit) => `Amount (${unit})`,
  refillHint: "Leave empty to use the default refill amount.",
  editorHub: "MedTrack hub",
  editorHubHelper: "Optional when you have only one MedTrack hub.",
  editorVersion: (v) => `Card version ${v}`,
  failed: "Something went wrong",
};

const ICONS = {
  check: '<path d="M20 6 9 17l-5-5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  skip: '<circle cx="12" cy="12" r="9"/><path d="M8 12h8"/>',
  alert:
    '<path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
  plus: '<path d="M12 5v14"/><path d="M5 12h14"/>',
};

const svg = (name, size = 18) =>
  `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`;

const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );

// Times arrive in Home Assistant's time zone; show their clock part as is.
const hhmm = (iso) => (iso ? iso.slice(11, 16) : "");
const num = (value) => String(Number(Number(value).toFixed(3)));

const errorText = (err) =>
  String(err?.message || err || T.failed).replace(/^Validation error: /, "");

const STYLE = `
  :host { display: block; }
  ha-card { padding: 16px; overflow: hidden; }
  button { font: inherit; color: inherit; }
  .header { display: flex; justify-content: space-between; align-items: flex-end; gap: 12px; margin-bottom: 16px; }
  .date { display: block; font-size: 13px; font-weight: 500; color: var(--secondary-text-color); letter-spacing: 0.02em; text-transform: uppercase; }
  h1 { margin: 4px 0 0; font-size: 28px; font-weight: 700; letter-spacing: -0.02em; line-height: 1.1; }
  .ring { position: relative; width: 64px; height: 64px; flex: none; }
  .ring svg { display: block; }
  .ring .track { stroke: var(--divider-color); }
  .ring .bar { stroke: var(--primary-color); transition: stroke-dasharray 0.3s; }
  .ring span { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font-size: 15px; font-weight: 700; }
  .banner { display: flex; gap: 10px; align-items: center; width: 100%; min-height: 44px; box-sizing: border-box; margin: 0 0 10px; padding: 12px 14px; border: none; border-radius: 16px; text-align: left; cursor: pointer;
    background: color-mix(in srgb, var(--warning-color) 16%, transparent); color: var(--primary-text-color); font-size: 14px; line-height: 1.35; }
  .banner svg { color: var(--warning-color); flex: none; }
  .banner strong { font-weight: 600; }
  .timeline { margin-top: 6px; }
  .group { display: flex; gap: 12px; }
  .rail { display: flex; flex-direction: column; align-items: center; width: 44px; flex: none; }
  .rail .time { font-size: 13px; font-weight: 600; color: var(--secondary-text-color); padding-top: 16px; }
  .rail .time.now { color: var(--warning-color); font-weight: 700; }
  .rail .line { flex-grow: 1; width: 2px; margin-top: 6px; background: var(--divider-color); }
  .items { flex-grow: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; padding-bottom: 12px; }
  .item { border: 1px solid var(--divider-color); border-radius: 16px; display: flex; flex-direction: column; }
  .item.active { border: 2px solid var(--warning-color); }
  .row { display: flex; align-items: center; gap: 12px; width: 100%; min-height: 44px; box-sizing: border-box; padding: 12px 14px; background: none; border: none; border-radius: 16px; text-align: left; }
  button.row { cursor: pointer; }
  .icon { width: 36px; height: 36px; border-radius: 18px; flex: none; box-sizing: border-box; display: flex; align-items: center; justify-content: center; }
  .icon.done { background: color-mix(in srgb, var(--primary-color) 14%, transparent); color: var(--primary-color); }
  .icon.active { background: color-mix(in srgb, var(--warning-color) 16%, transparent); color: var(--warning-color); }
  .icon.missed { background: color-mix(in srgb, var(--error-color) 14%, transparent); color: var(--error-color); }
  .icon.skipped { background: var(--secondary-background-color); color: var(--secondary-text-color); }
  .icon.upcoming { border: 2px dashed var(--divider-color); }
  .text { flex-grow: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  .name { font-size: 15px; font-weight: 600; overflow-wrap: anywhere; }
  .sub { font-size: 13px; color: var(--secondary-text-color); }
  .item.active .sub { color: var(--warning-color); font-weight: 500; }
  .item.missed .sub { color: var(--error-color); }
  .details { padding: 0 14px 12px 62px; font-size: 13px; color: var(--secondary-text-color); display: flex; flex-direction: column; gap: 2px; }
  .actions { display: flex; flex-wrap: wrap; gap: 8px; padding: 0 14px 14px; }
  .btn { flex: 1 1 auto; min-height: 44px; padding: 0 16px; border: 1px solid var(--divider-color); border-radius: 12px; background: none; color: var(--primary-text-color); font-size: 15px; font-weight: 500; cursor: pointer; }
  .btn.primary { flex-grow: 2; border: none; background: var(--primary-color); color: var(--text-primary-color, #fff); font-weight: 600; }
  .btn:disabled { opacity: 0.5; cursor: default; }
  .empty { text-align: center; padding: 24px 8px 12px; }
  .empty strong { display: block; font-size: 16px; margin-bottom: 4px; }
  .empty span { font-size: 14px; color: var(--secondary-text-color); }
  .empty .btn { margin-top: 12px; }
  dialog { border: none; border-radius: 20px; padding: 20px; width: min(360px, calc(100vw - 32px)); box-sizing: border-box;
    background: var(--ha-card-background, var(--card-background-color, #fff)); color: var(--primary-text-color); }
  dialog::backdrop { background: rgba(0, 0, 0, 0.45); }
  dialog h2 { margin: 0 0 8px; font-size: 18px; }
  dialog p { margin: 0 0 8px; font-size: 15px; line-height: 1.4; }
  dialog label { display: block; margin: 12px 0 6px; font-size: 14px; color: var(--secondary-text-color); }
  dialog input { width: 100%; box-sizing: border-box; min-height: 44px; padding: 0 12px; border: 1px solid var(--divider-color); border-radius: 12px;
    background: none; color: var(--primary-text-color); font: inherit; font-size: 17px; }
  dialog .hint { font-size: 13px; color: var(--secondary-text-color); margin-top: 6px; }
  dialog .buttons { display: flex; gap: 8px; margin-top: 16px; }
  dialog .buttons .btn { flex: 1; }
`;

class MedTrackTodayCard extends HTMLElement {
  constructor() {
    super();
    this._day = null;
    this._error = null;
    this._expanded = new Set();
    this._busy = false;
    this._seq = 0;
    this._seen = new Map();
    this._entityIds = [];
    this._entitiesRef = null;
    this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card><div id="content"></div></ha-card><dialog id="dialog"></dialog>`;
    this._content = this.shadowRoot.getElementById("content");
    this._dialog = this.shadowRoot.getElementById("dialog");
    this._content.addEventListener("click", (ev) => this._onClick(ev));
    this._onVisible = () => {
      if (document.visibilityState === "visible") this._fetch();
    };
  }

  // --- Home Assistant card API ------------------------------------------------

  static getConfigElement() {
    return document.createElement(`${CARD_TYPE}-editor`);
  }

  static async getStubConfig(hass) {
    try {
      const entries = await hass.callWS({ type: "config_entries/get", domain: DOMAIN });
      const entry = entries.find((e) => e.state === "loaded") || entries[0];
      return entry ? { entry_id: entry.entry_id } : {};
    } catch (_err) {
      return {};
    }
  }

  setConfig(config) {
    this._config = { ...config };
    this._day = null;
    if (this._hass) this._fetch();
    else this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this._entityChanges();
      this._fetch();
    } else if (this._entityChanges()) {
      clearTimeout(this._debounce);
      this._debounce = setTimeout(() => this._fetch(), 300);
    }
  }

  getCardSize() {
    return 2 + (this._day ? this._day.doses.length + this._day.refills.length : 2);
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }

  connectedCallback() {
    document.addEventListener("visibilitychange", this._onVisible);
    if (this._hass && this._day) this._fetch();
  }

  disconnectedCallback() {
    document.removeEventListener("visibilitychange", this._onVisible);
    clearTimeout(this._refreshTimer);
    clearTimeout(this._debounce);
  }

  // --- Data ---------------------------------------------------------------------

  /** Whether any MedTrack entity changed since the last call. */
  _entityChanges() {
    const hass = this._hass;
    if (hass.entities !== this._entitiesRef) {
      this._entitiesRef = hass.entities;
      this._entityIds = Object.values(hass.entities || {})
        .filter((e) => e.platform === DOMAIN)
        .map((e) => e.entity_id);
    }
    let changed = false;
    for (const id of this._entityIds) {
      const state = hass.states[id];
      if (this._seen.get(id) !== state) {
        this._seen.set(id, state);
        changed = true;
      }
    }
    return changed;
  }

  async _fetch() {
    if (!this._hass || !this._config) return;
    const seq = ++this._seq;
    const data = this._config.entry_id ? { config_entry_id: this._config.entry_id } : {};
    try {
      const result = await this._hass.callWS({
        type: "call_service",
        domain: DOMAIN,
        service: "get_day",
        service_data: data,
        return_response: true,
      });
      if (seq !== this._seq) return;
      this._day = result.response;
      this._error = null;
    } catch (err) {
      if (seq !== this._seq) return;
      this._error = errorText(err);
    }
    this._render();
    this._planRefresh();
  }

  /** Fetch again when the day changes by itself (server clock, not the phone's). */
  _planRefresh() {
    clearTimeout(this._refreshTimer);
    const day = this._day;
    if (!day || !day.refresh_at) return;
    const wait = Date.parse(day.refresh_at) - Date.parse(day.now) + 1000;
    this._refreshTimer = setTimeout(() => this._fetch(), Math.min(Math.max(wait, 1000), 6 * 3600 * 1000));
  }

  async _call(service, data = {}, target = undefined) {
    this._busy = true;
    this._render();
    try {
      await this._hass.callWS({
        type: "call_service",
        domain: DOMAIN,
        service,
        service_data: data,
        ...(target ? { target } : {}),
      });
    } catch (err) {
      this._toast(errorText(err));
    } finally {
      this._busy = false;
      await this._fetch();
    }
  }

  _toast(message) {
    this.dispatchEvent(
      new CustomEvent("hass-notification", { detail: { message }, bubbles: true, composed: true }),
    );
  }

  _med(id) {
    return this._day?.medications.find((m) => m.medication_id === id);
  }

  // --- Interaction ----------------------------------------------------------------

  _onClick(ev) {
    const el = ev.target.closest("[data-action]");
    if (!el || el.disabled) return;
    const { action, dose, med, key } = el.dataset;
    const target = (id) => ({ device_id: this._med(id)?.device_id });
    switch (action) {
      case "toggle":
        if (this._expanded.has(key)) this._expanded.delete(key);
        else this._expanded.add(key);
        this._render();
        break;
      case "take":
        this._call("take_dose", { dose_id: dose });
        break;
      case "skip":
        this._call("skip_dose", { dose_id: dose });
        break;
      case "snooze":
        this._call("snooze_dose", {}, target(med));
        break;
      case "undo":
        this._confirmUndo(med);
        break;
      case "refill":
        this._openRefill(med);
        break;
      case "retry":
        this._fetch();
        break;
    }
  }

  /** A small modal (native <dialog>: shows above everything, also on iOS). */
  _openDialog(html, onConfirm) {
    const dialog = this._dialog;
    dialog.innerHTML = `<form method="dialog">${html}</form>`;
    const form = dialog.querySelector("form");
    form.querySelector("[data-cancel]").addEventListener("click", () => dialog.close());
    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const values = Object.fromEntries(new FormData(form));
      dialog.close();
      onConfirm(values);
    });
    dialog.showModal();
    const input = form.querySelector("input");
    if (input) input.select();
  }

  _confirmUndo(medId) {
    const med = this._med(medId);
    if (!med || !med.undo) return;
    const undo = med.undo;
    let stock = "";
    if (undo.stock_change) {
      const sign = undo.stock_change > 0 ? "+" : "\u2212";
      stock = `<p>${esc(T.stockChange(sign, undo.stock_change_text))}</p>`;
    }
    this._openDialog(
      `<h2>${esc(T.undoTitle)}</h2><p>${esc(T.undoQuestion(undo.label, med.name))}</p>${stock}
       <div class="buttons"><button type="button" class="btn" data-cancel>${esc(T.btnCancel)}</button>
       <button type="submit" class="btn primary">${esc(T.btnUndo)}</button></div>`,
      async () => {
        // Undo always reverts the newest action: make sure it is still this one.
        await this._fetch();
        if (this._med(medId)?.undo?.record_id !== undo.record_id) {
          this._toast(T.undoChanged);
          return;
        }
        this._call("undo_last", {}, { device_id: med.device_id });
      },
    );
  }

  _openRefill(medId) {
    const med = this._med(medId);
    if (!med) return;
    const value = med.refill_amount == null ? "" : num(med.refill_amount);
    this._openDialog(
      `<h2>${esc(T.refillTitle(med.name))}</h2>
       <label for="amount">${esc(T.refillLabel(med.unit))}</label>
       <input id="amount" name="amount" type="number" inputmode="decimal" min="0" step="any" value="${esc(value)}">
       ${value === "" ? `<div class="hint">${esc(T.refillHint)}</div>` : ""}
       <div class="buttons"><button type="button" class="btn" data-cancel>${esc(T.btnCancel)}</button>
       <button type="submit" class="btn primary">${esc(T.btnRefill)}</button></div>`,
      ({ amount }) => {
        const parsed = parseFloat(String(amount).replace(",", "."));
        // Empty: MedTrack uses the default refill amount (or explains there is none).
        this._call("refill", parsed > 0 ? { amount: parsed } : {}, { device_id: med.device_id });
      },
    );
  }

  // --- Rendering ------------------------------------------------------------------

  _render() {
    const day = this._day;
    if (!day) {
      this._content.innerHTML = this._error
        ? `<div class="empty"><strong>${esc(this._error)}</strong>
           <button class="btn" data-action="retry">${esc(T.retry)}</button></div>`
        : `<div class="empty"><span>${esc(T.loading)}</span></div>`;
      return;
    }
    this._content.innerHTML = this._header(day) + this._banners(day) + this._body(day);
  }

  _header(day) {
    const lang = this._hass?.locale?.language || this._hass?.language || "en";
    let date = day.date;
    try {
      date = new Date(`${day.date}T12:00:00Z`).toLocaleDateString(lang, {
        weekday: "long",
        day: "numeric",
        month: "long",
        timeZone: "UTC",
      });
    } catch (_err) {
      /* keep the ISO date */
    }
    let ring = "";
    if (day.total > 0) {
      const circumference = 2 * Math.PI * 26;
      const filled = (circumference * day.taken) / day.total;
      ring = `<div class="ring" role="img" aria-label="${esc(T.ofTaken(day.taken, day.total))}">
        <svg width="64" height="64" viewBox="0 0 64 64">
          <circle class="track" cx="32" cy="32" r="26" fill="none" stroke-width="7"></circle>
          ${day.taken ? `<circle class="bar" cx="32" cy="32" r="26" fill="none" stroke-width="7" stroke-linecap="round" stroke-dasharray="${filled.toFixed(1)} ${circumference.toFixed(1)}" transform="rotate(-90 32 32)"></circle>` : ""}
        </svg><span aria-hidden="true">${day.taken}/${day.total}</span></div>`;
    }
    return `<div class="header"><div><span class="date">${esc(date)}</span><h1>${esc(T.today)}</h1></div>${ring}</div>`;
  }

  _banners(day) {
    return day.medications
      .filter((m) => m.low_stock && m.tracks_stock)
      .map((m) => {
        const days = m.days_remaining == null ? "" : `, ${T.aboutDays(Math.floor(m.days_remaining))}`;
        return `<button class="banner" data-action="refill" data-med="${esc(m.medication_id)}">
          ${svg("alert", 20)}<span><strong>${esc(T.runningLow(m.name))}</strong> \u2014 ${esc(T.left(m.stock_text) + days)}</span></button>`;
      })
      .join("");
  }

  _body(day) {
    if (!day.medications.length) {
      return `<div class="empty"><strong>${esc(T.noMedications)}</strong><span>${esc(T.noMedicationsHint)}</span></div>`;
    }
    const entries = [
      ...day.doses.map((d) => ({ time: d.time, html: this._doseItem(d), active: d.state === "pending" || d.state === "snoozed" })),
      ...day.refills.map((r) => ({ time: hhmm(r.at), html: this._refillItem(r), active: false })),
    ];
    if (!entries.length) {
      return `<div class="empty"><strong>${esc(T.noDoses)}</strong><span>${esc(T.noDosesHint)}</span></div>`;
    }
    entries.sort((a, b) => a.time.localeCompare(b.time));
    const groups = [];
    for (const entry of entries) {
      const last = groups[groups.length - 1];
      if (last && last.time === entry.time) {
        last.items.push(entry.html);
        last.active ||= entry.active;
      } else groups.push({ time: entry.time, items: [entry.html], active: entry.active });
    }
    return `<div class="timeline">${groups
      .map(
        (g, i) => `<div class="group"><div class="rail"><span class="time${g.active ? " now" : ""}">${esc(g.time)}</span>
          ${i < groups.length - 1 ? '<div class="line"></div>' : ""}</div>
          <div class="items">${g.items.join("")}</div></div>`,
      )
      .join("")}</div>`;
  }

  _button(label, action, attrs, primary = false) {
    return `<button class="btn${primary ? " primary" : ""}" data-action="${action}" ${attrs} ${this._busy ? "disabled" : ""}>${esc(label)}</button>`;
  }

  _doseItem(d) {
    const key = d.dose_id;
    const open = this._expanded.has(key);
    const doseAttr = `data-dose="${esc(d.dose_id)}"`;
    const medAttr = `data-med="${esc(d.medication_id)}"`;
    const reminder = Math.max(d.reminders_sent, 1);
    let icon = "";
    let cls = "";
    let sub = "";
    let buttons = [];
    const details = [];
    switch (d.state) {
      case "taken":
        [icon, cls, sub] = ["check", "done", `${d.quantity} \u00b7 ${T.taken(hhmm(d.taken_at))}`];
        break;
      case "taken_late":
        [icon, cls, sub] = ["check", "done", `${d.quantity} \u00b7 ${T.takenLate(hhmm(d.taken_at))}`];
        break;
      case "skipped":
        [icon, cls, sub] = ["skip", "skipped", `${d.quantity} \u00b7 ${T.skipped(hhmm(d.resolved_at))}`];
        break;
      case "missed":
        [icon, cls, sub] = ["alert", "missed", `${d.quantity} \u00b7 ${T.missed}`];
        if (d.can_take) buttons.push(this._button(T.btnTakenLate, "take", doseAttr, true));
        break;
      case "pending":
      case "snoozed":
        [icon, cls] = ["clock", "active"];
        sub =
          d.state === "pending"
            ? T.due(reminder, d.max_reminders)
            : T.snoozed(hhmm(d.next_reminder_at), reminder, d.max_reminders);
        details.push(d.quantity);
        if (d.can_take) buttons.push(this._button(T.btnTaken, "take", doseAttr, true));
        if (d.can_snooze) buttons.push(this._button(T.btnSnooze, "snooze", medAttr));
        if (d.can_skip) buttons.push(this._button(T.btnSkip, "skip", doseAttr));
        break;
      default: // scheduled
        [icon, cls, sub] = ["", "upcoming", `${d.quantity} \u00b7 ${T.upcoming}`];
        if (open) {
          if (d.can_take) buttons.push(this._button(T.btnTakeEarly, "take", doseAttr, true));
          if (d.can_skip) buttons.push(this._button(T.btnSkip, "skip", doseAttr));
          if (!d.can_take && !d.can_skip && d.early_from) details.push(T.takeFrom(hhmm(d.early_from)));
        }
    }
    if (d.can_undo && open) buttons.push(this._button(T.btnUndo, "undo", medAttr));
    const showsAll = cls === "active" || cls === "missed";
    if ((open || showsAll) && d.instructions) {
      if (cls === "active") details[0] = `${details[0]} \u00b7 ${d.instructions}`;
      else details.unshift(d.instructions);
    }
    const tappable = !showsAll && (d.state === "scheduled" || d.can_undo || d.instructions);
    return this._item({ key, icon, cls, name: d.medication, sub, details, buttons, tappable, open });
  }

  _refillItem(r) {
    const key = r.record_id;
    const open = this._expanded.has(key);
    const buttons = r.can_undo && open ? [this._button(T.btnUndo, "undo", `data-med="${esc(r.medication_id)}"`)] : [];
    return this._item({
      key,
      icon: "plus",
      cls: "done",
      name: r.medication,
      sub: T.refilled(r.quantity),
      details: [],
      buttons,
      tappable: r.can_undo,
      open,
    });
  }

  _item({ key, icon, cls, name, sub, details, buttons, tappable, open }) {
    const inner = `<span class="icon ${cls}">${icon ? svg(icon) : ""}</span>
      <span class="text"><span class="name">${esc(name)}</span><span class="sub">${esc(sub)}</span></span>`;
    const row = tappable
      ? `<button class="row" data-action="toggle" data-key="${esc(key)}" aria-expanded="${open}">${inner}</button>`
      : `<div class="row">${inner}</div>`;
    return `<div class="item ${cls}">${row}
      ${details.length ? `<div class="details">${details.map((t) => `<span>${esc(t)}</span>`).join("")}</div>` : ""}
      ${buttons.length ? `<div class="actions">${buttons.join("")}</div>` : ""}</div>`;
  }
}

class MedTrackTodayCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.schema = [{ name: "entry_id", selector: { config_entry: { integration: DOMAIN } } }];
      this._form.computeLabel = () => T.editorHub;
      this._form.computeHelper = () => T.editorHubHelper;
      this._form.addEventListener("value-changed", (ev) => {
        const config = { ...this._config, ...ev.detail.value };
        if (!config.entry_id) delete config.entry_id;
        this._config = config;
        this.dispatchEvent(new CustomEvent("config-changed", { detail: { config }, bubbles: true, composed: true }));
      });
      const version = document.createElement("p");
      version.style.cssText = "margin: 12px 0 0; font-size: 12px; color: var(--secondary-text-color);";
      version.textContent = T.editorVersion(VERSION);
      this.append(this._form, version);
    }
    this._form.hass = this._hass;
    this._form.data = this._config;
  }
}

if (!customElements.get(CARD_TYPE)) {
  customElements.define(CARD_TYPE, MedTrackTodayCard);
  customElements.define(`${CARD_TYPE}-editor`, MedTrackTodayCardEditor);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: CARD_TYPE,
    name: "MedTrack Today",
    description: "Today's doses with Taken, Snooze, Skip, Undo and Refill.",
    preview: true,
    documentationURL: "https://github.com/s-greg/ha-medtrack",
  });
  console.info(`%c MEDTRACK-TODAY-CARD %c ${VERSION} `, "background:#1F5FD1;color:#fff", "");
}
