import { Badge, Box, Flex, Grid, Text } from "@chakra-ui/react";

import SessionCard from "../components/SessionCard.jsx";
import { isoDate, startOfDay } from "../dates.js";

function DayCell({ date, month, booked, today, sessions, formats, times, onOpenSlot, onOpenDay }) {
  const iso = isoDate(date);
  const outside = date.getMonth() !== month;
  const past = iso < today;
  const classes = {
    borderWidth: "1px",
    borderColor: iso === today ? "red.muted" : "border.muted",
    rounded: "md",
    p: "1",
    minH: "7rem",
    opacity: past ? 0.45 : outside ? 0.55 : 1,
    bg: outside ? "bg.subtle" : undefined,
    cursor: "pointer",
  };

  return (
    <Box {...classes} onClick={() => onOpenDay(iso)}>
      <Flex align="baseline" gap="1" mb="1">
        <Text fontSize="2xs" fontVariantNumeric="tabular-nums" color="fg.muted">
          {date.getDate()}
        </Text>
        {booked && <Badge size="2xs" colorPalette="red" variant="subtle">booked</Badge>}
      </Flex>
      {/* A day can hold thirty-odd sessions, so the cell scrolls rather than growing. */}
      <Box maxH="11rem" overflowY="auto" onClick={(event) => event.stopPropagation()}>
        {sessions.map((session) => (
          <SessionCard
            key={session.session_id}
            session={session}
            date={iso}
            times={times}
            formats={formats}
            onOpenSlot={onOpenSlot}
            compact
          />
        ))}
      </Box>
    </Box>
  );
}

export default function MonthView({ calendar, days, cursor, formats, isVisible, times, onOpenSlot, onOpenDay }) {
  const byDate = new Map((calendar.days ?? []).map((day) => [day.date, day.sessions]));
  const booked = new Set(calendar.gate.booked_days);
  const today = isoDate(startOfDay(new Date()));
  const month = cursor.getMonth();

  return (
    <Box mt="2">
      <Grid templateColumns="repeat(7, minmax(0, 1fr))" gap="2" mb="1">
        {formats.weekdayLabels().map((label) => (
          <Text key={label} fontSize="2xs" color="fg.muted" textTransform="uppercase" letterSpacing="wider">
            {label}
          </Text>
        ))}
      </Grid>
      <Grid templateColumns="repeat(7, minmax(0, 1fr))" gap="2">
        {days.map((date) => {
          const iso = isoDate(date);
          const sessions = (byDate.get(iso) ?? []).filter(isVisible);
          return (
            <DayCell
              key={iso}
              date={date}
              month={month}
              booked={booked.has(iso)}
              today={today}
              sessions={sessions}
              formats={formats}
              times={times}
              onOpenSlot={onOpenSlot}
              onOpenDay={onOpenDay}
            />
          );
        })}
      </Grid>
    </Box>
  );
}
