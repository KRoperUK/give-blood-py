// Front end for the example server: a month calendar of every session across the
// donor's centres, with the API's bookability gate applied client-side so the
// filter can be toggled without another round trip.
//
// All formatting and escaping happens here — the API returns plain data.

const BOOKING_URL = "https://my.blood.co.uk/";

const node = (id) => document.getElementById(id);

// Values reach the page through innerHTML, so everything from the API goes through
// this. Null and empty render as an em dash rather than "null".
const escapeHtml = (value) => {
  if (value === null || value === undefined || value === "") return "&mdash;";
  return String(value).replace(/[&<>"]/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
  })[char]);
};

const formatDay = (iso) => iso
  ? new Date(iso).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })
  : null;

const formatWhen = (iso) => iso
  ? new Date(iso).toLocaleString(undefined, {
    weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  })
  : null;

// Local calendar dates. toISOString() would shift across midnight in any negative
// offset, which would put the wrong day on the grid.
const isoDate = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}` +
  `-${String(date.getDate()).padStart(2, "0")}`;

const slotKey = (sessionId, date, start, end) => `${sessionId}:${date}:${start}:${end}`;

const state = {
  cursor: new Date(),
  centre: "all",
  onlyBookable: true,
  calendar: null,
  selection: null,
  openPeriod: null,
  slots: new Map(),
};

let toastTimer = null;

function setToast(message) {
  node("toast").textContent = message;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node("toast").textContent = ""; }, 8000);
}

// --- filtering ------------------------------------------------------------

function sessionVisible(session) {
  if (state.onlyBookable && !session.bookable) return false;
  if (state.centre === "all") return true;
  if (state.centre === "preferred") return session.source.includes("preferred");
  return session.venue_id === state.centre;
}

function gateNote() {
  const calendar = state.calendar;
  const days = calendar.days || [];
  const total = days.reduce((count, day) => count + day.sessions.length, 0);
  const shown = days.reduce((count, day) => count + day.sessions.filter(sessionVisible).length, 0);
  const hidden = total - shown;

  const parts = [];
  if (calendar.gate.earliest) parts.push(`earliest bookable ${formatDay(calendar.gate.earliest)}`);
  if (calendar.gate.booked_days.length) parts.push(`${calendar.gate.booked_days.length} day(s) already booked`);
  parts.push(`searching to ${formatDay(calendar.limit)}`);
  parts.push(`${shown} of ${total} session(s)` + (hidden ? ` — ${hidden} hidden by the filter` : ""));
  if (calendar.degraded.length) parts.push(`${calendar.degraded.length} centre(s) failed`);
  return parts.join(" · ");
}

// --- rendering ------------------------------------------------------------

function renderDonor(summary) {
  const target = node("donor");
  if (summary.error) {
    target.innerHTML = `<p class="warn">${escapeHtml(summary.error)}</p>`;
    return;
  }

  const { donor, eligibility } = summary;
  const next = eligibility.next_appointment;
  const nextLine = next
    ? `<p>Next appointment: <strong>${escapeHtml(formatWhen(next.starts_at))}</strong> &mdash; ` +
      `${escapeHtml(next.venue_name)} (${escapeHtml(next.procedure)})</p>`
    : '<p class="muted">No appointment booked.</p>';
  const failover = summary.failover?.active
    ? `<p class="warn">Booking system notice: ${escapeHtml(summary.failover.header)} &mdash; ` +
      `${escapeHtml(summary.failover.content)}</p>`
    : "";
  const degraded = summary.degraded?.length
    ? `<p class="muted">Some reads degraded: ${escapeHtml(summary.degraded.join(", "))}</p>`
    : "";

  target.innerHTML = `
    <p><strong>${escapeHtml(donor.name || "Donor")}</strong> &middot; ${escapeHtml(donor.blood_group)} &middot;
      ${escapeHtml(donor.donation_credit)} credit(s) &middot; ${escapeHtml(donor.award_state || "no award yet")}</p>
    <p class="muted">Can donate from ${escapeHtml(formatDay(eligibility.can_donate_from))} &middot;
      can book from ${escapeHtml(formatDay(eligibility.can_book_from))}</p>
    ${nextLine}
    ${failover}
    ${degraded}`;
}

function renderAppointments(data) {
  const target = node("appointments");
  if (data.error) {
    target.innerHTML = `<p class="warn">${escapeHtml(data.error)}</p>`;
    return;
  }
  if (!data.appointments.length) {
    target.innerHTML = '<p class="muted">Nothing booked.</p>';
    return;
  }

  const rows = data.appointments.map((appointment) => `
    <tr>
      <td>${escapeHtml(formatWhen(appointment.starts_at))}</td>
      <td>${escapeHtml(appointment.procedure)}</td>
      <td>${escapeHtml(appointment.venue_name)}</td>
      <td>${escapeHtml(appointment.status)}</td>
    </tr>`).join("");

  target.innerHTML = `
    <table>
      <thead><tr><th>When</th><th>Procedure</th><th>Venue</th><th>Status</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function renderToolbar() {
  const calendar = state.calendar;
  if (!calendar) return;

  node("month-label").textContent = state.cursor.toLocaleDateString(undefined, { month: "long", year: "numeric" });

  const now = new Date();
  node("prev-month").disabled = state.cursor.getFullYear() === now.getFullYear() && state.cursor.getMonth() === now.getMonth();

  const options = [
    { value: "all", label: `All centres (${calendar.centres.length})` },
    { value: "preferred", label: "Preferred only" },
    ...calendar.centres.map((centre) => ({ value: centre.venue_id, label: `${centre.venue_id} — ${centre.name}` })),
  ];
  if (!options.some((option) => option.value === state.centre)) state.centre = "all";

  const select = node("centre-filter");
  const signature = options.map((option) => `${option.value}\u0000${option.label}`).join("|");
  if (select.dataset.signature !== signature) {
    select.innerHTML = options
      .map((option) => `<option value="${escapeHtml(option.value)}">${escapeHtml(option.label)}</option>`)
      .join("");
    select.dataset.signature = signature;
  }
  select.value = state.centre;
  node("only-bookable").checked = state.onlyBookable;
  node("gate-note").textContent = gateNote();
}

function renderCalendar() {
  const target = node("calendar");
  const calendar = state.calendar;
  if (!calendar) {
    target.innerHTML = '<p class="muted">Loading&hellip;</p>';
    return;
  }
  if (calendar.outside_window) {
    target.innerHTML = '<p class="muted">Nothing is searched this far ahead — this server looks as far as ' +
      `${escapeHtml(formatDay(calendar.limit))}. Raise <span class="mono">--days</span> to go further.</p>`;
    return;
  }

  const byDate = new Map((calendar.days || []).map((day) => [day.date, day.sessions]));
  const booked = new Set(calendar.gate.booked_days);
  const today = isoDate(new Date());
  const year = state.cursor.getFullYear();
  const month = state.cursor.getMonth();

  // Monday-first, padded out to whole weeks.
  const leading = (new Date(year, month, 1).getDay() + 6) % 7;
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const cellCount = Math.ceil((leading + daysInMonth) / 7) * 7;

  const cells = [];
  let visible = 0;
  for (let index = 0; index < cellCount; index += 1) {
    const date = new Date(year, month, index - leading + 1);
    const iso = isoDate(date);
    const sessions = (byDate.get(iso) || []).filter(sessionVisible);
    visible += sessions.length;

    const classes = ["day"];
    if (date.getMonth() !== month) classes.push("is-outside");
    if (iso < today) classes.push("is-past");
    if (iso === today) classes.push("is-today");

    const chips = sessions.map((session) => {
      const free = session.free_slots === null ? "slot count not disclosed" : `${session.free_slots} free`;
      const selected = state.selection !== null && state.selection.date === iso
        && state.selection.sessionId === session.session_id;
      return `<button type="button" class="chip${selected ? " is-selected" : ""}" data-date="${iso}" ` +
        `data-session="${escapeHtml(session.session_id)}" ` +
        `title="${escapeHtml(session.venue_name)} — ${escapeHtml(free)}">` +
        `${escapeHtml(session.venue_id)}${session.free_slots === null ? "" : ` <strong>${escapeHtml(session.free_slots)}</strong>`}` +
        `</button>`;
    }).join("");

    cells.push(`<div class="${classes.join(" ")}">
        <span class="daynum">${date.getDate()}</span>${booked.has(iso) ? '<span class="booked">booked</span>' : ""}
        <div class="chips">${chips}</div>
      </div>`);
  }

  const totalSessions = (calendar.days || []).reduce((count, day) => count + day.sessions.length, 0);
  // Without this the calendar just looks broken on a month where the filter hides
  // everything, which is the normal case for the current month.
  const notice = visible === 0 && totalSessions > 0
    ? `<p class="muted">Nothing bookable in ${escapeHtml(state.cursor.toLocaleDateString(undefined, { month: "long" }))}. ` +
      (calendar.gate.earliest ? `Earliest is ${escapeHtml(formatDay(calendar.gate.earliest))} — ` : "") +
      `untick "only show what I can book" to see all ${escapeHtml(totalSessions)} session(s).</p>`
    : "";

  const weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((day) => `<span>${day}</span>`).join("");
  target.innerHTML = notice + `<div class="weekdays">${weekdays}</div><div class="grid">${cells.join("")}</div>`;
}

function renderTimes(session, date, period) {
  const entry = state.slots.get(slotKey(session.session_id, date, period.start, period.end));
  if (!entry) return "";
  if (entry.loading) return '<div class="times"><span class="muted">loading times&hellip;</span></div>';
  if (entry.error) return `<div class="times"><span class="warn">${escapeHtml(entry.error)}</span></div>`;
  if (entry.data.error) return `<div class="times"><span class="warn">${escapeHtml(entry.data.error)}</span></div>`;

  const data = entry.data;
  if (!data.slots.length) return '<div class="times"><span class="muted">No times left in this period.</span></div>';

  const times = data.slots.map((slot) => `<button type="button" class="time${slot.last_one_available ? " last-one" : ""}" ` +
    `data-venue="${escapeHtml(session.venue_id)}" data-date="${escapeHtml(date)}" data-clock="${escapeHtml(slot.clock)}" ` +
    `title="${escapeHtml(slot.procedure || "")}${slot.last_one_available ? " — last one available" : ""}">` +
    `${escapeHtml(slot.clock)}</button>`).join("");

  const clashing = data.clashing_appointments.length
    ? `<p class="warn">Clashes with ${escapeHtml(data.clashing_appointments.map((a) => formatWhen(a.starts_at)).join(", "))}</p>`
    : "";
  return `<div class="times">${times}</div>${clashing}`;
}

function renderDayDetail() {
  const target = node("day-detail");
  const selection = state.selection;
  node("slot-hint").hidden = !selection;

  if (!selection || !state.calendar) {
    target.hidden = true;
    target.innerHTML = "";
    return;
  }

  const day = (state.calendar.days || []).find((entry) => entry.date === selection.date);
  const sessions = (day ? day.sessions : []).filter(sessionVisible);
  target.hidden = false;

  if (!sessions.length) {
    target.innerHTML = `<h3>${escapeHtml(formatDay(selection.date))}</h3>` +
      '<p class="muted">Nothing shown here under the current filters.</p>';
    return;
  }

  const blocks = sessions.map((session) => {
    const periods = session.periods.map((period) => {
      const open = state.openPeriod === slotKey(session.session_id, selection.date, period.start, period.end);
      const free = period.free_slots === null ? "" : ` &middot; ${escapeHtml(period.free_slots)} free`;
      return `<button type="button" class="period${open ? " is-open" : ""}" ` +
        `data-session="${escapeHtml(session.session_id)}" data-date="${selection.date}" ` +
        `data-start="${escapeHtml(period.start)}" data-end="${escapeHtml(period.end)}">` +
        `${escapeHtml(period.start)}&ndash;${escapeHtml(period.end)}${free}</button>`;
    }).join("");

    const open = session.periods.find((period) =>
      state.openPeriod === slotKey(session.session_id, selection.date, period.start, period.end));
    const bookability = session.bookable
      ? '<span class="ok">bookable</span>'
      : '<span class="warn">outside your bookable window</span>';
    const free = session.free_slots === null ? "slot count not disclosed" : `${session.free_slots} free slot(s)`;

    return `<div class="session${session.session_id === selection.sessionId ? " is-selected" : ""}">
        <h4>${escapeHtml(session.venue_id)} &middot; ${escapeHtml(session.venue_name)}` +
        `<span class="badge">${escapeHtml(session.source)}</span></h4>
        <p class="muted">${escapeHtml(free)} &middot; ${bookability}</p>
        <div class="periods">${periods}</div>
        ${open ? renderTimes(session, selection.date, open) : ""}
      </div>`;
  }).join("");

  target.innerHTML = `<h3>${escapeHtml(formatDay(selection.date))}</h3>${blocks}`;
}

// --- data -----------------------------------------------------------------

async function loadCalendar(refresh = false) {
  const first = new Date(state.cursor.getFullYear(), state.cursor.getMonth(), 1);
  const last = new Date(state.cursor.getFullYear(), state.cursor.getMonth() + 1, 0);
  const query = new URLSearchParams({ start: isoDate(first), end: isoDate(last) });
  if (refresh) query.set("refresh", "1");

  state.calendar = await fetch(`/api/calendar?${query}`).then((response) => response.json());
  state.openPeriod = null;
  state.slots.clear();
  renderToolbar();
  renderCalendar();
  renderDayDetail();
}

async function loadSlots(key, sessionId, date, start, end) {
  state.slots.set(key, { loading: true });
  renderDayDetail();
  try {
    const query = new URLSearchParams({ session_id: sessionId, date, start, end });
    const data = await fetch(`/api/slots?${query}`).then((response) => response.json());
    state.slots.set(key, { data });
  } catch (err) {
    state.slots.set(key, { error: String(err) });
  }
  renderDayDetail();
}

async function load(refresh = false) {
  const suffix = refresh ? "?refresh=1" : "";
  const fetchJson = (path) => fetch(path + suffix).then((response) => response.json());
  const updated = node("updated");
  updated.textContent = "refreshing…";

  try {
    const [summary, appointments] = await Promise.all([fetchJson("/api/summary"), fetchJson("/api/appointments")]);
    renderDonor(summary);
    renderAppointments(appointments);
    await loadCalendar(refresh);
    updated.textContent = `updated ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    updated.textContent = `refresh failed: ${err}`;
  }
}

// --- interaction ----------------------------------------------------------

function openSlot(venueId, date, clock) {
  // Opened synchronously: awaiting the clipboard first would break the user-gesture
  // requirement and get this blocked as a popup.
  window.open(BOOKING_URL, "_blank", "noopener");
  const reference = `${venueId} · ${date} · ${clock}`;
  // clipboard is undefined outside a secure context — reachable by binding the
  // server to a LAN address over plain http, so the reference is shown either way.
  const copied = navigator.clipboard?.writeText(reference);
  if (!copied) {
    setToast(`${reference} — opening the NHS booking site (clipboard needs https)`);
    return;
  }
  copied
    .then(() => setToast(`Copied ${reference} — opening the NHS booking site`))
    .catch(() => setToast(`${reference} — opening the NHS booking site (copy refused)`));
}

function shiftMonth(delta) {
  state.cursor = new Date(state.cursor.getFullYear(), state.cursor.getMonth() + delta, 1);
  state.selection = null;
  loadCalendar();
}

node("calendar").addEventListener("click", (event) => {
  const chip = event.target.closest(".chip");
  if (!chip) return;
  state.selection = { date: chip.dataset.date, sessionId: chip.dataset.session };
  renderCalendar();
  renderDayDetail();
});

node("day-detail").addEventListener("click", (event) => {
  const time = event.target.closest(".time");
  if (time) {
    openSlot(time.dataset.venue, time.dataset.date, time.dataset.clock);
    return;
  }
  const period = event.target.closest(".period");
  if (!period) return;

  const key = slotKey(period.dataset.session, period.dataset.date, period.dataset.start, period.dataset.end);
  if (state.openPeriod === key) {
    state.openPeriod = null;
    renderDayDetail();
    return;
  }
  state.openPeriod = key;
  if (state.slots.has(key)) {
    renderDayDetail();
    return;
  }
  loadSlots(key, period.dataset.session, period.dataset.date, period.dataset.start, period.dataset.end);
});

node("prev-month").addEventListener("click", () => shiftMonth(-1));
node("next-month").addEventListener("click", () => shiftMonth(1));
node("this-month").addEventListener("click", () => {
  state.cursor = new Date();
  state.selection = null;
  loadCalendar();
});
node("refresh").addEventListener("click", () => load(true));

node("centre-filter").addEventListener("change", (event) => {
  state.centre = event.target.value;
  renderToolbar();
  renderCalendar();
  renderDayDetail();
});

node("only-bookable").addEventListener("change", (event) => {
  state.onlyBookable = event.target.checked;
  renderToolbar();
  renderCalendar();
  renderDayDetail();
});

load(false);
