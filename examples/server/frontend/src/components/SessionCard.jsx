import { Badge, Box, Button, Flex, HStack, Spinner, Text } from "@chakra-ui/react";

/**
 * The times inside one period.
 *
 * Times are the volatile part of the API — someone else can take one between two
 * loads — so a period that came back empty says so rather than showing nothing.
 */
function TimeChips({ period, times, date, session, formats, onOpenSlot }) {
  const entry = times[period.key];

  if (entry === undefined) return <Spinner size="2xs" color="fg.muted" />;
  if (entry.error) return <Text fontSize="2xs" color="fg.error">{entry.error}</Text>;
  if (!entry.slots?.length) return <Text fontSize="2xs" color="fg.muted">no times left</Text>;

  return (
    <Flex wrap="wrap" gap="1">
      {entry.slots.map((slot) => (
        <Button
          key={slot.time}
          size="2xs"
          variant={slot.last_one_available ? "surface" : "outline"}
          colorPalette={slot.last_one_available ? "red" : undefined}
          fontVariantNumeric="tabular-nums"
          title={`${slot.procedure ?? "slot"}${slot.last_one_available ? " — last one available" : ""}`}
          onClick={() => onOpenSlot({
            venueId: session.venue_id,
            date,
            clock: slot.clock,
            procedure: slot.procedure,
          })}
        >
          {formats.formatClock(slot.time)}
        </Button>
      ))}
    </Flex>
  );
}

/**
 * One clinic session, with every period and every time in it.
 *
 * `compact` trims it down for a month cell: the venue name moves to the tooltip
 * and the times scroll, because a day can hold thirty sessions.
 */
export function SessionCard({ session, date, times, formats, onOpenSlot, compact = false }) {
  const free = session.free_slots === null ? "count not disclosed" : `${session.free_slots} free`;

  return (
    <Box
      borderWidth="1px"
      borderColor="border.muted"
      rounded="md"
      p={compact ? "1" : "2"}
      mb="1"
      opacity={session.bookable ? 1 : 0.6}
      title={`${session.venue_name} (${session.source}) — ${free}`}
    >
      <Flex align="center" justify="space-between" gap="1">
        <Text fontSize="2xs" fontWeight="semibold" fontVariantNumeric="tabular-nums">
          {session.venue_id}
        </Text>
        <HStack gap="1">
          {!compact && <Badge size="2xs" variant="subtle">{session.source}</Badge>}
          <Text fontSize="2xs" color="fg.muted" whiteSpace="nowrap">
            {session.free_slots === null ? "?" : session.free_slots} free
          </Text>
        </HStack>
      </Flex>

      {!compact && (
        <Text fontSize="2xs" color="fg.muted" truncate>
          {session.venue_name}
        </Text>
      )}

      <Flex direction="column" gap="1" mt="1">
        {session.periods.map((period) => (
          <Box key={`${period.start}-${period.end}`}>
            <HStack gap="1" mb="0.5">
              <Text fontSize="2xs" color="fg.muted" fontVariantNumeric="tabular-nums">
                {formats.formatClock(period.start)}–{formats.formatClock(period.end)}
              </Text>
              {period.free_slots !== null && (
                <Badge size="2xs" variant="outline">{period.free_slots}</Badge>
              )}
            </HStack>
            <TimeChips
              period={period}
              times={times}
              date={date}
              session={session}
              formats={formats}
              onOpenSlot={onOpenSlot}
            />
          </Box>
        ))}
      </Flex>
    </Box>
  );
}

export default SessionCard;
