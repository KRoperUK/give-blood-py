import {
  Badge,
  Box,
  Button,
  Checkbox,
  Container,
  Flex,
  Heading,
  HStack,
  Separator,
  Spinner,
  Table,
  Text,
} from "@chakra-ui/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { fetchAppointments, fetchCalendar, fetchSlotBatch, fetchSummary } from "./api.js";
import { addDays, addMonths, daysFor, isoDate, parseIso, periodKey, rangeFor, startOfDay } from "./dates.js";
import { createFormatters, resolveLocale } from "./format.js";
import DayView from "./views/DayView.jsx";
import MonthView from "./views/MonthView.jsx";
import WeekView from "./views/WeekView.jsx";

const BOOKING_URL = "https://my.blood.co.uk/";

const VIEWS = [
  { id: "month", label: "Month" },
  { id: "week", label: "Week" },
  { id: "day", label: "Day" },
];

//: Periods per batch request. The server caps a batch at 200 and bounds how many
//: upstream calls run at once; this only keeps progress visible between chunks.
const BATCH_SIZE = 60;

export default function App() {
  const formats = useMemo(
    () => createFormatters(resolveLocale({
      search: window.location.search,
      languages: navigator.languages ?? [],
      language: navigator.language ?? "",
    })),
    [],
  );

  const [summary, setSummary] = useState(null);
  const [appointments, setAppointments] = useState(null);
  const [calendar, setCalendar] = useState(null);
  const [times, setTimes] = useState({});
  const [progress, setProgress] = useState(null);
  const [notice, setNotice] = useState("");
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);

  const [view, setView] = useState("month");
  const [cursor, setCursor] = useState(() => startOfDay(new Date()));
  const [hidden, setHidden] = useState(() => new Set());
  const [onlyBookable, setOnlyBookable] = useState(true);

  //: Periods already asked for, so re-rendering never re-requests one.
  const requested = useRef(new Set());

  const isVisible = useCallback(
    (session) => !hidden.has(session.venue_id) && (!onlyBookable || session.bookable),
    [hidden, onlyBookable],
  );

  const [start, end] = useMemo(() => rangeFor(view, cursor), [view, cursor]);
  const days = useMemo(() => daysFor(view, cursor), [view, cursor]);

  const loadAll = useCallback(async ({ from, to, refresh = false }) => {
    const [nextSummary, nextAppointments, nextCalendar] = await Promise.all([
      fetchSummary(refresh),
      fetchAppointments(refresh),
      fetchCalendar({ start: isoDate(from), end: isoDate(to), refresh }),
    ]);
    setSummary(nextSummary);
    setAppointments(nextAppointments);
    setCalendar(nextCalendar);
  }, []);

  useEffect(() => {
    let cancelled = false;
    loadAll({ from: start, to: end })
      .catch((err) => !cancelled && setFailure(String(err)));
    return () => {
      cancelled = true;
    };
  }, [loadAll, start, end]);

  // Times are fetched for whatever the current view actually shows, in batches.
  useEffect(() => {
    if (!calendar || calendar.outside_window) return;

    const wanted = [];
    for (const day of calendar.days ?? []) {
      for (const session of day.sessions) {
        if (!isVisible(session)) continue;
        for (const period of session.periods) {
          const key = periodKey(session.session_id, day.date, period.start, period.end);
          if (!requested.current.has(key)) {
            wanted.push({
              key,
              session_id: session.session_id,
              date: day.date,
              start: period.start,
              end: period.end,
            });
          }
        }
      }
    }
    if (wanted.length === 0) return;

    for (const period of wanted) requested.current.add(period.key);
    let cancelled = false;

    (async () => {
      let done = 0;
      setProgress({ done, total: wanted.length });
      for (let index = 0; index < wanted.length; index += BATCH_SIZE) {
        const group = wanted.slice(index, index + BATCH_SIZE);
        try {
          // eslint-disable-next-line no-await-in-loop -- sequential on purpose: the
          // server bounds upstream concurrency, and progress stays meaningful.
          const data = await fetchSlotBatch(group);
          if (cancelled) return;
          setTimes((previous) => {
            const next = { ...previous };
            for (const period of data.periods) next[period.key] = period;
            return next;
          });
        } catch (err) {
          if (!cancelled) setFailure(String(err));
        }
        done += group.length;
        if (!cancelled) setProgress({ done, total: wanted.length });
      }
      if (!cancelled) setProgress(null);
    })();

    return () => {
      cancelled = true;
    };
  }, [calendar, isVisible]);

  const fullRefresh = useCallback(async () => {
    // Drops everything, including the slot times: the point of the button is to
    // see the live picture, not the one from a few minutes ago.
    requested.current.clear();
    setTimes({});
    setFailure(null);
    setNotice("");
    setBusy(true);
    try {
      await loadAll({ from: start, to: end, refresh: true });
    } catch (err) {
      setFailure(String(err));
    } finally {
      setBusy(false);
    }
  }, [loadAll, start, end]);

  const openSlot = useCallback(({ venueId, date, clock }) => {
    // Opened synchronously: awaiting the clipboard first would break the
    // user-gesture requirement and get this blocked as a popup.
    window.open(BOOKING_URL, "_blank", "noopener");
    // Deliberately not localised — this is pasted into the booking site, so it
    // stays an ISO date and a 24-hour clock.
    const reference = `${venueId} · ${date} · ${clock}`;
    const copied = navigator.clipboard?.writeText(reference);
    if (!copied) {
      setNotice(`${reference} — opening the NHS booking site (clipboard needs https)`);
      return;
    }
    copied
      .then(() => setNotice(`Copied ${reference} — opening the NHS booking site`))
      .catch(() => setNotice(`${reference} — opening the NHS booking site (copy refused)`));
  }, []);

  const openDay = useCallback((iso) => {
    setCursor(parseIso(iso));
    setView("day");
  }, []);

  const shift = (delta) => {
    if (view === "day") setCursor((current) => addDays(current, delta));
    else if (view === "week") setCursor((current) => addDays(current, delta * 7));
    else setCursor((current) => addMonths(current, delta));
  };

  const centres = calendar?.centres ?? [];
  const shownSessions = (calendar?.days ?? []).reduce(
    (total, day) => total + day.sessions.filter(isVisible).length,
    0,
  );
  const allSessions = (calendar?.days ?? []).reduce((total, day) => total + day.sessions.length, 0);

  const rangeLabel = view === "month"
    ? formats.formatMonth(cursor)
    : view === "day"
      ? formats.formatDate(days[0])
      : `${formats.formatShortDate(days[0])} – ${formats.formatShortDate(days[days.length - 1])}`;

  const gate = calendar?.gate;
  const asOf = calendar?.cache?.as_of ? formats.formatDateTime(calendar.cache.as_of) : null;
  const next = summary?.eligibility?.next_appointment;

  return (
    <Container maxW="7xl" py="6">
      <Flex justify="space-between" align="flex-start" gap="4" wrap="wrap">
        <Box>
          <Heading size="lg">Give Blood</Heading>
          <Text color="fg.muted" fontSize="sm">
            Read-only view of your appointments and availability. Booking is not possible from here.
          </Text>
        </Box>
        <HStack gap="3">
          {progress && <Spinner size="xs" />}
          {progress && (
            <Text fontSize="xs" color="fg.muted" fontVariantNumeric="tabular-nums">
              times {progress.done}/{progress.total}
            </Text>
          )}
          <Button size="sm" variant="surface" loading={busy} onClick={fullRefresh}>
            Full refresh
          </Button>
        </HStack>
      </Flex>

      {failure && <Text mt="2" fontSize="sm" color="fg.error">error: {failure}</Text>}

      <Box mt="4">
        {summary === null && <Spinner size="sm" />}
        {summary?.error && <Text fontSize="sm" color="fg.error">{summary.error}</Text>}
        {summary && !summary.error && (
          <>
            <Text fontSize="sm">
              <strong>{summary.donor.name || "Donor"}</strong> · {summary.donor.blood_group} ·{" "}
              {summary.donor.donation_credit} credit(s) · {summary.donor.award_state || "no award yet"}
            </Text>
            <Text fontSize="sm" color="fg.muted">
              Can donate from {formats.formatDate(summary.eligibility.can_donate_from)} · can book from{" "}
              {formats.formatDate(summary.eligibility.can_book_from)}
            </Text>
            <Text fontSize="sm" mt="1">
              {next
                ? `Next appointment: ${formats.formatDateTime(next.starts_at)} — ${next.venue_name} (${next.procedure})`
                : "No appointment booked."}
            </Text>
            {summary.failover?.active && (
              <Text fontSize="sm" color="fg.error" mt="1">
                Booking system notice: {summary.failover.header} — {summary.failover.content}
              </Text>
            )}
          </>
        )}
      </Box>

      <Heading size="sm" mt="6" mb="2">Upcoming appointments</Heading>
      {appointments === null && <Spinner size="sm" />}
      {appointments?.appointments?.length === 0 && (
        <Text fontSize="sm" color="fg.muted">Nothing booked.</Text>
      )}
      {appointments?.appointments?.length > 0 && (
        <Table.Root size="sm">
          <Table.Header>
            <Table.Row>
              <Table.ColumnHeader>When</Table.ColumnHeader>
              <Table.ColumnHeader>Procedure</Table.ColumnHeader>
              <Table.ColumnHeader>Venue</Table.ColumnHeader>
              <Table.ColumnHeader>Status</Table.ColumnHeader>
            </Table.Row>
          </Table.Header>
          <Table.Body>
            {appointments.appointments.map((appointment) => (
              <Table.Row key={`${appointment.session_id}-${appointment.starts_at}`}>
                <Table.Cell whiteSpace="nowrap">{formats.formatDateTime(appointment.starts_at)}</Table.Cell>
                <Table.Cell>{appointment.procedure}</Table.Cell>
                <Table.Cell>{appointment.venue_name}</Table.Cell>
                <Table.Cell>{appointment.status}</Table.Cell>
              </Table.Row>
            ))}
          </Table.Body>
        </Table.Root>
      )}

      <Separator my="6" />

      <Flex align="center" gap="4" wrap="wrap">
        <HStack gap="1">
          {VIEWS.map((entry) => (
            <Button
              key={entry.id}
              size="xs"
              variant={view === entry.id ? "solid" : "ghost"}
              onClick={() => setView(entry.id)}
            >
              {entry.label}
            </Button>
          ))}
        </HStack>

        <HStack gap="1">
          <Button size="xs" variant="outline" onClick={() => shift(-1)} aria-label={`Previous ${view}`}>‹</Button>
          <Button size="xs" variant="ghost" onClick={() => setCursor(startOfDay(new Date()))}>Today</Button>
          <Button size="xs" variant="outline" onClick={() => shift(1)} aria-label={`Next ${view}`}>›</Button>
          <Text fontSize="sm" fontWeight="semibold" minW="14rem">
            {rangeLabel}
          </Text>
        </HStack>

        <Checkbox.Root
          size="sm"
          checked={onlyBookable}
          onCheckedChange={(details) => setOnlyBookable(details.checked === true)}
        >
          <Checkbox.HiddenInput />
          <Checkbox.Control />
          <Checkbox.Label fontSize="sm">Only show what I can book</Checkbox.Label>
        </Checkbox.Root>
      </Flex>

      <Flex align="center" gap="1" wrap="wrap" mt="2">
        <Text fontSize="2xs" color="fg.muted" textTransform="uppercase" letterSpacing="wider">Centres</Text>
        <Button size="2xs" variant="ghost" onClick={() => setHidden(new Set())}>All</Button>
        <Button
          size="2xs"
          variant="ghost"
          onClick={() => setHidden(new Set(
            centres.filter((centre) => !centre.source.includes("preferred")).map((centre) => centre.venue_id),
          ))}
        >
          Preferred only
        </Button>
        {centres.map((centre) => {
          const off = hidden.has(centre.venue_id);
          return (
            <Button
              key={centre.venue_id}
              size="2xs"
              variant={off ? "ghost" : "subtle"}
              colorPalette={off ? undefined : "red"}
              textDecoration={off ? "line-through" : undefined}
              opacity={off ? 0.6 : 1}
              title={`${centre.name} (${centre.source})`}
              onClick={() => setHidden((previous) => {
                const next = new Set(previous);
                if (next.has(centre.venue_id)) next.delete(centre.venue_id);
                else next.add(centre.venue_id);
                return next;
              })}
            >
              {centre.venue_id}
            </Button>
          );
        })}
      </Flex>

      <Text fontSize="xs" color="fg.muted" mt="2">
        {gate?.earliest ? `Earliest bookable ${formats.formatDate(gate.earliest)} · ` : ""}
        {gate?.booked_days?.length ? `${gate.booked_days.length} day(s) already booked · ` : ""}
        {calendar ? `searching to ${formats.formatDate(calendar.limit)} · ` : ""}
        {calendar ? `${centres.length - hidden.size} of ${centres.length} centre(s) · ` : ""}
        {shownSessions} of {allSessions} session(s)
        {asOf ? ` · as of ${asOf}` : ""}
      </Text>

      {notice && <Text fontSize="xs" mt="1" color="fg.muted">{notice}</Text>}

      {calendar === null && <Spinner size="sm" mt="4" />}

      {calendar?.outside_window && (
        <Text fontSize="sm" color="fg.muted" mt="4">
          Nothing is searched this far ahead — this server looks as far as{" "}
          {formats.formatDate(calendar.limit)}.
        </Text>
      )}

      {shownSessions === 0 && allSessions > 0 && !calendar?.outside_window && (
        <Text fontSize="sm" color="fg.muted" mt="4">
          Nothing shown in {rangeLabel} under the current filters
          {hidden.size ? " — some centres are switched off" : ""}
          {onlyBookable && gate?.earliest ? `, and the earliest bookable day is ${formats.formatDate(gate.earliest)}` : ""}
          . All {allSessions} session(s) appear once the filters allow it.
        </Text>
      )}

      {calendar && !calendar.outside_window && (
        <>
          {calendar.degraded?.length > 0 && (
            <Text fontSize="xs" color="fg.error" mt="2">
              {calendar.degraded.length} centre(s) failed: {calendar.degraded.join(", ")}
            </Text>
          )}
          {view === "month" && (
            <MonthView
              calendar={calendar}
              days={days}
              cursor={cursor}
              formats={formats}
              isVisible={isVisible}
              times={times}
              onOpenSlot={openSlot}
              onOpenDay={openDay}
            />
          )}
          {view === "week" && (
            <WeekView
              calendar={calendar}
              days={days}
              formats={formats}
              isVisible={isVisible}
              times={times}
              onOpenSlot={openSlot}
              onOpenDay={openDay}
            />
          )}
          {view === "day" && (
            <DayView
              calendar={calendar}
              days={days}
              formats={formats}
              isVisible={isVisible}
              times={times}
              onOpenSlot={openSlot}
            />
          )}
        </>
      )}
    </Container>
  );
}
