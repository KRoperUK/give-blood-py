import { Badge, Box, Flex, Grid, Text } from "@chakra-ui/react";

import SessionCard from "../components/SessionCard.jsx";
import { isoDate, startOfDay } from "../dates.js";

export default function WeekView({ calendar, days, formats, isVisible, times, onOpenSlot, onOpenDay }) {
  const byDate = new Map((calendar.days ?? []).map((day) => [day.date, day.sessions]));
  const booked = new Set(calendar.gate.booked_days);
  const today = isoDate(startOfDay(new Date()));

  return (
    <Grid templateColumns="repeat(7, minmax(0, 1fr))" gap="2" mt="2">
      {days.map((date) => {
        const iso = isoDate(date);
        const sessions = (byDate.get(iso) ?? []).filter(isVisible);
        return (
          <Box
            key={iso}
            borderWidth="1px"
            borderColor={iso === today ? "red.muted" : "border.muted"}
            rounded="md"
            p="1.5"
            minH="16rem"
          >
            <Flex align="baseline" justify="space-between" gap="1" mb="1" cursor="pointer" onClick={() => onOpenDay(iso)}>
              <Text fontSize="2xs" fontWeight="semibold">{formats.formatShortDate(date)}</Text>
              {booked.has(iso) && <Badge size="2xs" colorPalette="red" variant="subtle">booked</Badge>}
            </Flex>
            {sessions.length === 0 && (
              <Text fontSize="2xs" color="fg.muted">nothing free</Text>
            )}
            {sessions.map((session) => (
              <SessionCard
                key={session.session_id}
                session={session}
                date={iso}
                times={times}
                formats={formats}
                onOpenSlot={onOpenSlot}
              />
            ))}
          </Box>
        );
      })}
    </Grid>
  );
}
