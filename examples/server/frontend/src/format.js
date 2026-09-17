// Locale-aware date and time formatting.
//
// The API returns ISO strings carrying the venue's own offset (Europe/London), so
// every value here is a real instant and Intl can do the work: a browser set to
// another locale reads the calendar naturally.
//
// The fallback is the UK rather than the runtime default. The venues are UK
// venues, and an unqualified "en" carries no region — honouring it as-is would
// hand a British donor US ordering.

export const DEFAULT_LOCALE = "en-GB";

// 2024-01-01 was a Monday, which is where these calendars start the week.
const WEEK_REFERENCE = Array.from({ length: 7 }, (_, index) => new Date(2024, 0, 1 + index));

function ordinalSuffix(day) {
  const remainder = day % 100;
  if (remainder >= 11 && remainder <= 13) return "th";
  return { 1: "st", 2: "nd", 3: "rd" }[day % 10] ?? "th";
}

function toDate(value) {
  if (value instanceof Date) return value;
  if (typeof value !== "string" || value === "") return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/**
 * Choose a locale: an explicit override, then the browser's preference, then the
 * UK. A bare "en" has no region, so it falls back rather than guessing.
 */
export function resolveLocale({ search = "", languages = [], language = "" } = {}) {
  const override = new URLSearchParams(search).get("locale");
  if (override) return override;
  const preferred = [...languages].find((tag) => tag) ?? language;
  return !preferred || preferred.toLowerCase() === "en" ? DEFAULT_LOCALE : preferred;
}

export function createFormatters(locale) {
  const english = locale.toLowerCase().startsWith("en");
  const dates = new Intl.DateTimeFormat(locale, { day: "numeric", month: "long", year: "numeric" });
  const shortDates = new Intl.DateTimeFormat(locale, { weekday: "short", day: "numeric", month: "short" });
  const months = new Intl.DateTimeFormat(locale, { month: "long", year: "numeric" });
  const weekdays = new Intl.DateTimeFormat(locale, { weekday: "short" });
  // CLDR gives en-GB a 24-hour clock, but "12:30pm" is what was asked for, so a
  // 12-hour clock is requested for English and left to the locale otherwise.
  const times = new Intl.DateTimeFormat(locale, {
    hour: "numeric",
    minute: "2-digit",
    ...(english ? { hour12: true } : {}),
  });

  // Rebuilt from parts so the meridiem can be lowercased and closed up: Intl
  // renders "12:30 pm" and "12:30 PM", neither of which is "12:30pm".
  const clock = (date) => times
    .formatToParts(date)
    .map((part) => (part.type === "dayPeriod" ? part.value.toLowerCase() : part.value))
    .join("")
    .replace(/\s+([ap]m)$/i, "$1");

  const formatDate = (value) => {
    const date = toDate(value);
    if (date === null) return null;
    // Intl has no ordinal day for any locale, so English gets its suffix added to
    // the day part in place — the surrounding order and punctuation still come
    // from the locale's own pattern.
    return dates
      .formatToParts(date)
      .map((part) => (part.type === "day" && english ? `${part.value}${ordinalSuffix(Number(part.value))}` : part.value))
      .join("");
  };

  return {
    locale,
    /** "1st January 2001" */
    formatDate,
    /** "1st January 2001, 12:30pm" */
    formatDateTime: (value) => {
      const date = toDate(value);
      return date === null ? null : `${formatDate(date)}, ${clock(date)}`;
    },
    /** "Mon 1 Jan" — for grid cells and other tight spots. */
    formatShortDate: (value) => {
      const date = toDate(value);
      return date === null ? null : shortDates.format(date);
    },
    /** "January 2001" */
    formatMonth: (value) => {
      const date = toDate(value);
      return date === null ? null : months.format(date);
    },
    /**
     * A bare clock from the API — ``HHMM`` on session periods, ``THHMM`` on
     * appointments and slots — rendered as a time.
     */
    formatClock: (value) => {
      const digits = typeof value === "string" ? value.replace(/^T/, "") : "";
      if (!/^\d{4}$/.test(digits)) return value ?? null;
      const date = new Date();
      date.setHours(Number(digits.slice(0, 2)), Number(digits.slice(2)), 0, 0);
      return clock(date);
    },
    /** Monday-first, in the locale's own names. */
    weekdayLabels: () => WEEK_REFERENCE.map((date) => weekdays.format(date)),
  };
}
