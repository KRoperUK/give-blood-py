import { Badge, Box, Flex, Heading, HStack, Text } from "@chakra-ui/react";

import SessionCard from "../components/SessionCard.jsx";
import { isoDate, startOfDay } from "../dates.js";

export default function DayView({ calendar, days, formats, isVisible, times, onOpenSlot }) {
  const iso = isoDate(days[0] ?? startOfDay(new Date()));
  const day = (calendar.days ?? []).find((entry) => entry.date === iso);
  const sessions = (day?.sessions ?? []).filter(isVisible);
  const booked = calendar.gate.booked_days.includes(iso);

  const freeSlots = sessions.reduce((total, session) => total + (session.free_slots ?? 0), 0);
  const disclosed = sessions.some((session) => session.free_slots !== null);

  return (
    <Box mt="2">
      <Flex align="baseline" gap="2" mb="2">
        <Heading size="sm">{formats.formatDate(iso)}</Heading>
        {booked && <Badge size="sm" colorPalette="red" variant="subtle">already booked</Badge>}
        <Text fontSize="xs" color="fg.muted">
          {sessions.length} session(s)
          {disclosed ? ` · ${freeSlots} free slot(s)` : " · counts not disclosed"}
        </Text>
      </Flex>

      {sessions.length === 0 && (
        <Text fontSize="sm" color="fg.muted">
          Nothing shown for this day under the current filters.
        </Text>
      )}

      {/* Wider than a grid cell, so the times sit on one line per period. */}
      <HStack align="start" gap="3" wrap="wrap">
        {sessions.map((session) => (
          <Box key={session.session_id} minW="18rem" flex="1 1 18rem" maxW="32rem">
            <SessionCard
              session={session}
              date={iso}
              times={times}
              formats={formats}
              onOpenSlot={onOpenSlot}
            />
          </Box>
        ))}
      </HStack>
    </Box>
  );
}
