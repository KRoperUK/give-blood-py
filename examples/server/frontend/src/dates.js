// Calendar arithmetic on local dates, kept apart from format.js so the rendering
// rules stay free of timezone maths.

export const isoDate = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}` +
  `-${String(date.getDate()).padStart(2, "0")}`;

export const parseIso = (iso) => {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(year, month - 1, day);
};

export const startOfDay = (date) => new Date(date.getFullYear(), date.getMonth(), date.getDate());

export const addDays = (date, days) => new Date(date.getFullYear(), date.getMonth(), date.getDate() + days);

export const addMonths = (date, months) => new Date(date.getFullYear(), date.getMonth() + months, 1);

/** Monday-first, matching the grids. */
export const startOfWeek = (date) => addDays(startOfDay(date), -((date.getDay() + 6) % 7));

export const monthBounds = (date) => [
  new Date(date.getFullYear(), date.getMonth(), 1),
  new Date(date.getFullYear(), date.getMonth() + 1, 0),
];

export const weekBounds = (date) => {
  const start = startOfWeek(date);
  return [start, addDays(start, 6)];
};

/** The range of days a view needs data for. */
export function rangeFor(view, cursor) {
  if (view === "day") return [startOfDay(cursor), startOfDay(cursor)];
  if (view === "week") return weekBounds(cursor);
  return monthBounds(cursor);
}

/** The days a view lays out, in order. Month and week are padded to whole weeks. */
export function daysFor(view, cursor) {
  if (view === "day") return [startOfDay(cursor)];
  if (view === "week") {
    const [start] = weekBounds(cursor);
    return Array.from({ length: 7 }, (_, index) => addDays(start, index));
  }
  const [first, last] = monthBounds(cursor);
  const leading = (first.getDay() + 6) % 7;
  const cells = Math.ceil((leading + last.getDate()) / 7) * 7;
  const gridStart = addDays(first, -leading);
  return Array.from({ length: cells }, (_, index) => addDays(gridStart, index));
}

/** Identifies one period. Mirrors the key the server builds. */
export const periodKey = (sessionId, date, start, end) => `${sessionId}:${date}:${start}:${end}`;
