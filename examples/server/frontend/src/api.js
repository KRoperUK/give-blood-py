// The API this UI sits on. Every call here is read-only: nothing in this file can
// change an appointment.
//
// `refresh` maps to ?refresh=1, which is what the server's full-refresh button
// uses to skip its cache instead of serving a value that is a few minutes old.

const asJson = (response) => response.json();

const ifRefresh = (refresh) => (refresh ? "?refresh=1" : "");

export const fetchSummary = (refresh = false) => fetch(`/api/summary${ifRefresh(refresh)}`).then(asJson);

export const fetchAppointments = (refresh = false) => fetch(`/api/appointments${ifRefresh(refresh)}`).then(asJson);

export function fetchCalendar({ start, end, refresh = false }) {
  const query = new URLSearchParams({ start, end });
  if (refresh) query.set("refresh", "1");
  return fetch(`/api/calendar?${query}`).then(asJson);
}

/**
 * Times for many periods in one call.
 *
 * A month of cells is a couple of hundred periods, and each one is a separate
 * upstream request, so they are handed to the server in batches: it bounds how
 * many run at once and caches each period individually.
 */
export function fetchSlotBatch(periods, refresh = false) {
  return fetch(`/api/slots/batch${ifRefresh(refresh)}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ periods }),
  }).then(asJson);
}
