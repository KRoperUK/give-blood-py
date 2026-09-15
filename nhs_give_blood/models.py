"""Pydantic models for the NHS Give Blood app API.

Wire names are camelCase; Python attributes are snake_case and every model
accepts either (``populate_by_name=True``). ``extra="allow"`` is deliberate:
this is a reverse-engineered private API, so a newly-added field must not raise
— it stays reachable via ``model_extra`` and shows up in diagnostics.

Two wire quirks are normalised here rather than pushed onto callers:

* ``0001-01-01T00:00:00`` (.NET ``DateTime.MinValue``) means "no date" and
  becomes ``None``.
* Naive datetimes are venue-local, so they are localised to Europe/London.
  Every datetime this module returns is timezone-aware.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from .const import (
    AWARD_TIERS,
    FREE_SLOTS_UNKNOWN,
    NULL_DATETIME,
    VENUE_TIMEZONE,
)

_TZ = ZoneInfo(VENUE_TIMEZONE)

__all__ = [
    "AccountDetails",
    "Address",
    "Appointment",
    "Award",
    "AwardsData",
    "Donation",
    "DonationHistory",
    "DonorMessage",
    "Eligibility",
    "FailoverBanner",
    "FeatureFlags",
    "LoginResponse",
    "MessageBundle",
    "NearestVenue",
    "Period",
    "PreferredVenue",
    "Serology",
    "Session",
    "SessionSlots",
    "Slot",
    "VenueSearchResponse",
    "VenueSearchResult",
    "VenueSummary",
    "VersionCheck",
    "localise",
    "parse_api_datetime",
    "parse_wire_time",
]


def parse_api_datetime(value: Any) -> datetime | None:
    """Parse an API datetime, mapping the .NET min-value sentinel to ``None``.

    Naive results are localised to Europe/London, because every date the API
    emits is venue-local wall-clock time.
    """
    if value is None or value == "" or value == NULL_DATETIME:
        return None
    if isinstance(value, datetime):
        return localise(value)
    if isinstance(value, date):
        return localise(datetime.combine(value, time.min))
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.year <= 1:
        return None
    return localise(parsed)


def localise(value: datetime) -> datetime:
    """Attach Europe/London to a naive datetime; leave aware datetimes alone."""
    if value.tzinfo is None:
        return value.replace(tzinfo=_TZ)
    return value


def parse_wire_time(value: str | None) -> time | None:
    """Parse the API's two clock formats: ``T1730`` and ``1130``.

    Returns ``None`` for anything unparseable rather than raising — a malformed
    clock on one appointment must not sink a whole poll.
    """
    if not value:
        return None
    digits = value.lstrip("Tt")
    if len(digits) != 4 or not digits.isdigit():
        return None
    hour, minute = int(digits[:2]), int(digits[2:])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour, minute)


def _coerce_float(value: Any) -> float | None:
    """Best-effort float conversion; the API sends lat/long as strings."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int | None:
    """Best-effort int conversion; ``freeSlots`` arrives as a string."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_list(value: Any) -> Any:
    """Turn an explicit ``null`` into an empty list.

    The API is inconsistent about empty collections: ``notes``, ``lines`` and
    ``periods`` come back as ``[]`` in some responses and ``null`` in others for
    the same field. Callers should never have to care, so ``None`` is normalised
    to ``[]`` rather than being modelled as ``list | None``.
    """
    return [] if value is None else value


ApiDateTime = Annotated[datetime | None, BeforeValidator(parse_api_datetime)]
ApiFloat = Annotated[float | None, BeforeValidator(_coerce_float)]
ApiInt = Annotated[int | None, BeforeValidator(_coerce_int)]
ApiStrList = Annotated[list[str], BeforeValidator(_coerce_list)]

#: ``Slot.time`` is a wire field named after the clock type, so the name ``time``
#: is shadowed inside that class body. Alias it to keep the accessor's return
#: annotation resolvable.
_Clock = time


class _Base(BaseModel):
    """Shared config: alias population plus forward-compatible extras."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class Address(_Base):
    """A postal address as returned for donors and venues."""

    type: str | None = None
    company_name: str | None = Field(default=None, alias="companyName")
    lines: ApiStrList = Field(default_factory=list)
    postcode: str | None = None
    latitude: ApiFloat = None
    longitude: ApiFloat = None

    @property
    def one_line(self) -> str:
        """Render the address as a single comma-separated line."""
        parts = [*(line for line in self.lines if line), self.postcode]
        return ", ".join(part.strip() for part in parts if part)


class VenueSummary(_Base):
    """A donation venue.

    ``is_*_supported`` describes what the *venue* can collect, which is not the
    same as what is bookable on a given session.
    """

    venue_id: str | None = Field(default=None, alias="venueId")
    venue_name: str | None = Field(default=None, alias="venueName")
    donor_preferred: bool = Field(default=False, alias="donorPreferred")
    external_location: str | None = Field(default=None, alias="externalLocation")
    internal_location: str | None = Field(default=None, alias="internalLocation")
    address: Address | None = None
    latitude: ApiFloat = None
    longitude: ApiFloat = None
    notes: ApiStrList = Field(default_factory=list)
    is_whole_blood_supported: bool = Field(default=False, alias="isWholeBloodSupported")
    is_plasma_supported: bool = Field(default=False, alias="isPlasmaSupported")
    is_platelet_supported: bool = Field(default=False, alias="isPlateletSupported")
    is_donor_centre: bool = Field(default=False, alias="isDonorCentre")

    @property
    def display_name(self) -> str:
        """Venue name with its sub-location, e.g. "Town Hall (Main Hall)"."""
        name = self.venue_name or self.venue_id or "Unknown venue"
        if self.internal_location and self.internal_location not in name:
            return f"{name} ({self.internal_location})"
        return name


class PreferredVenue(_Base):
    """A venue on the donor's account payload (``accountDetails.venues``).

    Deliberately not :class:`VenueSummary`. The wire shape differs in ways that
    matter: the identifier is ``venueID`` with a capital ``ID`` rather than
    ``venueId``, it carries ``venPref`` and ``gridReference``, and it has none of
    the ``is*Supported`` flags. Forcing it into ``VenueSummary`` would mean
    aliasing the identifier and silently inventing the missing fields.
    """

    venue_id: str | None = Field(default=None, alias="venueID")
    ven_pref: str | None = Field(default=None, alias="venPref")
    venue_name: str | None = Field(default=None, alias="venueName")
    external_location: str | None = Field(default=None, alias="externalLocation")
    internal_location: str | None = Field(default=None, alias="internalLocation")
    address: Address | None = None
    grid_reference: str | None = Field(default=None, alias="gridReference")
    latitude: ApiFloat = None
    longitude: ApiFloat = None
    notes: ApiStrList = Field(default_factory=list)


class Period(_Base):
    """A bookable window within a session.

    ``free_slots`` is -1 when the API declines to disclose a count; use
    :attr:`has_known_free_slots` before trusting it.
    """

    start_time: str | None = Field(default=None, alias="startTime")
    end_time: str | None = Field(default=None, alias="endTime")
    free_slots: ApiInt = Field(default=None, alias="freeSlots")
    available_slots: int | None = Field(default=None, alias="availableSlots")

    @property
    def has_known_free_slots(self) -> bool:
        """True when ``free_slots`` is a real count rather than the sentinel."""
        return self.free_slots is not None and self.free_slots != FREE_SLOTS_UNKNOWN

    @property
    def opens_at(self) -> time | None:
        """Period start as a ``time``."""
        return parse_wire_time(self.start_time)

    @property
    def closes_at(self) -> time | None:
        """Period end as a ``time``."""
        return parse_wire_time(self.end_time)


class Session(_Base):
    """A clinic session at a venue on a given day.

    ``venue`` is populated when the session is returned standalone, and ``None``
    when nested under a venue that already carries it — read it through
    :class:`Appointment` rather than assuming presence.
    """

    session_id: str | None = Field(default=None, alias="sessionId")
    session_date: ApiDateTime = Field(default=None, alias="sessionDate")
    venue: VenueSummary | None = None
    periods: Annotated[list[Period], BeforeValidator(_coerce_list)] = Field(default_factory=list)
    session_status: str | None = Field(default=None, alias="sessionStatus")
    booking_flag: str | None = Field(default=None, alias="bookingFlag")
    appointment_status: str | None = Field(default=None, alias="appointmentStatus")
    availability: str | None = None
    weighting_factor: float | None = Field(default=None, alias="weightingFactor")
    notes: ApiStrList = Field(default_factory=list)

    @property
    def status_combo(self) -> str:
        """The app's 4-char session state key.

        Mirrors the app's ``buildSessionStatusCombo``: session status,
        appointment status, availability and booking flag concatenated. The app
        looks the result up in a server-supplied table; this library exposes the
        raw key rather than guessing at the table.
        """
        return "".join(
            part or "-" for part in (self.session_status, self.appointment_status, self.availability, self.booking_flag)
        )

    @property
    def total_free_slots(self) -> int | None:
        """Sum of disclosed free slots, or ``None`` if none were disclosed."""
        known = [p.free_slots for p in self.periods if p.has_known_free_slots and p.free_slots is not None]
        return sum(known) if known else None


class Appointment(_Base):
    """A booked donation appointment."""

    status: str | None = None
    time: str | None = None
    procedure_code: str | None = Field(default=None, alias="procedureCode")
    procedure_type: str | None = Field(default=None, alias="procedureType")
    procedure_description: str | None = Field(default=None, alias="procedureDescription")
    cancellation_code: str | None = Field(default=None, alias="cancellationCode")
    session: Session | None = None

    @property
    def starts_at(self) -> datetime | None:
        """Appointment start as an aware datetime, or ``None`` if incomplete.

        Built from the session date plus the appointment's own ``THHMM`` clock;
        the session date itself always has a midnight time component, so it is
        never a usable start time on its own.
        """
        if self.session is None or self.session.session_date is None:
            return None
        clock = parse_wire_time(self.time)
        if clock is None:
            return self.session.session_date
        return self.session.session_date.replace(hour=clock.hour, minute=clock.minute)

    @property
    def venue(self) -> VenueSummary | None:
        """The venue this appointment is at, if the API supplied it."""
        return self.session.venue if self.session else None

    @property
    def session_id(self) -> str | None:
        """Session identifier, needed to reschedule or cancel."""
        return self.session.session_id if self.session else None

    @property
    def is_cancelled(self) -> bool:
        """True once the API has attached a cancellation code."""
        return bool(self.cancellation_code)


class Eligibility(_Base):
    """When the donor may next donate, and next book.

    These differ: ``next_possible_donation_date`` is the clinical deferral
    expiry, ``next_possible_appointment_date`` also accounts for booking
    windows and existing appointments.
    """

    next_possible_donation_date: ApiDateTime = Field(default=None, alias="nextPossibleDonationDate")
    next_possible_appointment_date: ApiDateTime = Field(default=None, alias="nextPossibleAppointmentDate")


class SearchDatesFrom(_Base):
    """Earliest date the donor may search for each procedure type."""

    donation_intent: ApiDateTime = Field(default=None, alias="donationIntent")
    whole_blood: ApiDateTime = Field(default=None, alias="wholeBlood")
    plasma: ApiDateTime = None


class Serology(_Base):
    """Blood-typing detail. ``short_hand`` is an internal antigen code."""

    short_hand: str | None = Field(default=None, alias="shortHand")
    ro_enabled: bool = Field(default=False, alias="roEnabled")


class Award(_Base):
    """A single donation milestone."""

    is_achieved: bool = Field(default=False, alias="isAchieved")
    title: str | None = None
    awarded_date: ApiDateTime = Field(default=None, alias="awardedDate")
    credit_criteria: int | None = Field(default=None, alias="creditCriteria")

    @property
    def is_tier(self) -> bool:
        """True for the named tiers (Bronze…Ruby) rather than credit counts."""
        return self.title in AWARD_TIERS


class AwardsData(_Base):
    """The donor's award/milestone state."""

    registration_date: ApiDateTime = Field(default=None, alias="registrationDate")
    award_state: str | None = Field(default=None, alias="awardState")
    show_as_achievement: bool = Field(default=False, alias="showAsAchievement")
    total_credits: int = Field(default=0, alias="totalCredits")
    total_awards: int = Field(default=0, alias="totalAwards")
    awards: Annotated[list[Award], BeforeValidator(_coerce_list)] = Field(default_factory=list)
    highest_achieved: Award | None = Field(default=None, alias="highestAchieved")

    @property
    def next_award(self) -> Award | None:
        """The nearest unachieved milestone by credit threshold."""
        pending = [a for a in self.awards if not a.is_achieved and a.credit_criteria is not None]
        return min(pending, key=lambda a: a.credit_criteria or 0) if pending else None

    @property
    def credits_to_next_award(self) -> int | None:
        """Credits still needed for :attr:`next_award`, floored at zero."""
        nxt = self.next_award
        if nxt is None or nxt.credit_criteria is None:
            return None
        return max(0, nxt.credit_criteria - self.total_credits)

    @property
    def next_tier(self) -> Award | None:
        """The nearest unachieved *named* tier, ignoring credit-count awards."""
        pending = [a for a in self.awards if not a.is_achieved and a.is_tier and a.credit_criteria is not None]
        return min(pending, key=lambda a: a.credit_criteria or 0) if pending else None


class NearestVenue(_Base):
    """A venue plus the donor-relative distance/next-session metadata."""

    venue: VenueSummary | None = None
    session_day_count: int | None = Field(default=None, alias="sessionDayCount")
    venue_distance: ApiFloat = Field(default=None, alias="venueDistance")
    venue_distance_from_home: ApiFloat = Field(default=None, alias="venueDistanceFromHome")
    date_of_next_session: ApiDateTime = Field(default=None, alias="dateOfNextSession")
    is_donor_centre: bool = Field(default=False, alias="isDonorCentre")
    is_community_centre: bool = Field(default=False, alias="isCommunityCentre")
    is_whole_blood_supported: bool = Field(default=False, alias="isWholeBloodSupported")
    is_plasma_supported: bool = Field(default=False, alias="isPlasmaSupported")
    is_platelet_supported: bool = Field(default=False, alias="isPlateletSupported")


class Telephone(_Base):
    """A donor contact number. ``type`` is H(ome)/M(obile)/W(ork)."""

    type: str | None = None
    number: str | None = None


class Email(_Base):
    """A donor email address."""

    type: str | None = None
    address: str | None = None


class Forenames(_Base):
    """Split forename fields as stored by NHSBT."""

    first_forename: str | None = Field(default=None, alias="firstForename")
    second_forename: str | None = Field(default=None, alias="secondForename")
    other_forenames: str | None = Field(default=None, alias="otherForenames")


class AccountDetails(_Base):
    """The donor account payload from ``/api/account/v2/details``.

    This is the widest response in the API and doubles as the login payload's
    ``accountDetails``. It carries directly identifying data (name, address,
    phone, donor id) — treat it accordingly.
    """

    donor_id: str | None = Field(default=None, alias="donorID")
    title: str | None = None
    forenames: Forenames | None = None
    surname: str | None = None
    blood_group: str | None = Field(default=None, alias="bloodGroup")
    serology: Serology | None = None
    gender: str | None = None
    gender_identity: str | None = Field(default=None, alias="genderIdentity")
    sex_at_birth: str | None = Field(default=None, alias="sexAtBirth")
    ethnic_origin: str | None = Field(default=None, alias="ethnicOrigin")
    ethnic_origin_description: str | None = Field(default=None, alias="ethnicOriginDescription")
    date_of_birth: ApiDateTime = Field(default=None, alias="dateOfBirth")
    registration_date: ApiDateTime = Field(default=None, alias="registrationDate")

    donation_credit: int = Field(default=0, alias="donationCredit")
    eligibility: Eligibility | None = None
    search_dates_from: SearchDatesFrom | None = Field(default=None, alias="searchDatesFrom")
    has_donation_intent: bool = Field(default=False, alias="hasDonationIntent")
    has_previous_donations: bool = Field(default=False, alias="hasPreviousDonations")
    procedure_type: str | None = Field(default=None, alias="procedureType")
    procedure_code: str | None = Field(default=None, alias="procedureCode")
    procedure_description: str | None = Field(default=None, alias="procedureDescription")
    show_booking_cta: bool = Field(default=False, alias="showBookingCTA")
    show_welcome_page: bool = Field(default=False, alias="showWelcomePage")
    is_platelet_plus: bool = Field(default=False, alias="isPlateletPlus")
    refer_to_call_centre: str | None = Field(default=None, alias="referToCallCentre")
    email_change_pending: bool = Field(default=False, alias="emailChangePending")
    last_donated_venue_id: str | None = Field(default=None, alias="lastDonatedVenueId")

    appointments: Annotated[list[Appointment], BeforeValidator(_coerce_list)] = Field(default_factory=list)
    awards_data: AwardsData | None = Field(default=None, alias="awardsData")
    nearest_plasma_venue: NearestVenue | None = Field(default=None, alias="nearestPlasmaVenue")
    registration_venue: NearestVenue | None = Field(default=None, alias="registrationVenue")

    #: Donation-intent fields, populated for donors who have registered an intent to
    #: give a specific component. All three are null for an ordinary whole-blood or
    #: apheresis donor, which is why they are typed permissively.
    donation_intent_type: str | None = Field(default=None, alias="donationIntentType")
    donation_intent_code: str | None = Field(default=None, alias="donationIntentCode")
    donation_intent_description: str | None = Field(default=None, alias="donationIntentDescription")
    registration_intent: str | None = Field(default=None, alias="registrationIntent")

    #: Single-letter operational flags. NHSBT publishes no mapping for these; the
    #: values seen are all "N", so they are passed through rather than guessed at.
    #: See https://github.com/KRoperUK/give-blood-py/issues/15
    new_or_return: str | None = Field(default=None, alias="newOrReturn")
    print_dhc: str | None = Field(default=None, alias="printDHC")
    check_venue: str | None = Field(default=None, alias="checkVenue")
    priority_p: str | None = Field(default=None, alias="priorityP")
    priority_b: str | None = Field(default=None, alias="priorityB")

    #: An internal diagnostic echo, null in every response observed.
    response_from_api: Any | None = Field(default=None, alias="responseFromApi")
    #: A session the API suggests booking. Null for every account observed so far, so
    #: the shape is unknown and it stays untyped deliberately rather than guessed.
    #: See https://github.com/KRoperUK/give-blood-py/issues/16
    suggested_session: Any | None = Field(default=None, alias="suggestedSession")

    addresses: Annotated[list[Address], BeforeValidator(_coerce_list)] = Field(default_factory=list)
    telephones: Annotated[list[Telephone], BeforeValidator(_coerce_list)] = Field(default_factory=list)
    emails: Annotated[list[Email], BeforeValidator(_coerce_list)] = Field(default_factory=list)
    venues: Annotated[list[PreferredVenue], BeforeValidator(_coerce_list)] = Field(default_factory=list)
    language: str | None = None
    correspondence: str | None = None

    @property
    def full_name(self) -> str | None:
        """Best-effort display name, or ``None`` when nothing is populated."""
        first = self.forenames.first_forename if self.forenames else None
        parts = [part for part in (first, self.surname) if part]
        return " ".join(parts) or None

    @property
    def home_address(self) -> Address | None:
        """The address flagged ``H``, falling back to the first one."""
        for address in self.addresses:
            if address.type == "H":
                return address
        return self.addresses[0] if self.addresses else None

    @property
    def next_appointment(self) -> Appointment | None:
        """Soonest non-cancelled appointment with a resolvable start time."""
        dated = [a for a in self.appointments if a.starts_at is not None and not a.is_cancelled]
        return min(dated, key=lambda a: a.starts_at or datetime.max.replace(tzinfo=_TZ)) if dated else None

    @property
    def can_donate_from(self) -> datetime | None:
        """Clinical eligibility date, or ``None`` if the API omitted it."""
        return self.eligibility.next_possible_donation_date if self.eligibility else None


class LoginResponse(_Base):
    """The ``/api/auth/v2/login`` payload: token pair plus the account."""

    access_token: str = Field(alias="accessToken")
    refresh_token: str = Field(alias="refreshToken")
    account_details: AccountDetails | None = Field(default=None, alias="accountDetails")


class Donation(_Base):
    """A historical donation.

    ``type`` is a one-letter outcome code (A/B/L/R observed). NHSBT does not
    publish the mapping, so it is passed through verbatim rather than guessed
    at — see ``docs/api-reference.md``.
    """

    donation_id: str | None = Field(default=None, alias="donationId")
    session: Session | None = None
    type: str | None = None

    @property
    def donated_at(self) -> datetime | None:
        """Date of the donation, from its session."""
        return self.session.session_date if self.session else None

    @property
    def venue_name(self) -> str | None:
        """Venue the donation happened at, when supplied."""
        venue = self.session.venue if self.session else None
        return venue.display_name if venue else None


class DonationHistory(_Base):
    """``/api/account/donation-history``.

    ``has_further_donations`` means the API truncated the list — the count here
    is a lower bound, not the donor's lifetime total. Use
    ``AccountDetails.donation_credit`` for the authoritative credit count.
    """

    donor_id: str | None = Field(default=None, alias="donorID")
    has_further_donations: bool = Field(default=False, alias="hasFurtherDonations")
    donation: Annotated[list[Donation], BeforeValidator(_coerce_list)] = Field(default_factory=list)

    @property
    def most_recent(self) -> Donation | None:
        """Latest donation by session date."""
        dated = [d for d in self.donation if d.donated_at is not None]
        return max(dated, key=lambda d: d.donated_at or datetime.min.replace(tzinfo=_TZ)) if dated else None


class DonorMessage(_Base):
    """A targeted in-app message.

    ``blood_groups`` scopes the message; ``["All"]`` means everyone. The API
    returns every message regardless of the caller's group, so filter with
    :meth:`applies_to`.
    """

    id: str | None = None
    type: str | None = None
    title: str | None = None
    text: str | None = None
    show_only_once: bool = Field(default=False, alias="showOnlyOnce")
    blood_groups: ApiStrList = Field(default_factory=list, alias="bloodGroups")

    def applies_to(self, blood_group: str | None) -> bool:
        """True if this message targets ``blood_group`` (or everyone)."""
        if not self.blood_groups or "All" in self.blood_groups:
            return True
        return blood_group is not None and blood_group in self.blood_groups


class MessageBundle(_Base):
    """``/api/messages``, grouped by where the app displays each message."""

    donor_messages: Annotated[list[DonorMessage], BeforeValidator(_coerce_list)] = Field(
        default_factory=list, alias="donorMessages"
    )
    end_of_booking: Annotated[list[DonorMessage], BeforeValidator(_coerce_list)] = Field(
        default_factory=list, alias="endOfBooking"
    )
    end_of_sign_up: Annotated[list[DonorMessage], BeforeValidator(_coerce_list)] = Field(
        default_factory=list, alias="endOfSignUp"
    )

    def for_blood_group(self, blood_group: str | None) -> list[DonorMessage]:
        """Donor messages that target ``blood_group``."""
        return [m for m in self.donor_messages if m.applies_to(blood_group)]


class FeatureFlags(_Base):
    """Server-side feature switches for the app.

    ``failover`` here is the *switch*; the banner content comes from
    ``/api/features/failover``.
    """

    appointment_request_beta_banner: bool = Field(default=False, alias="appointmentRequestBetaBanner")
    chatbot: bool = False
    check_in: bool = Field(default=False, alias="checkIn")
    failover: bool = False
    par_full_sessions: bool = Field(default=False, alias="parFullSessions")
    waiting_list: bool = Field(default=False, alias="waitingList")


class FailoverBanner(_Base):
    """The "booking system unavailable" notice.

    ``is_active`` is the one field that matters: when true, NHSBT has taken the
    booking system down and writes will fail.
    """

    header: str | None = None
    content: str | None = None
    is_active: bool = Field(default=False, alias="isActive")


class VersionCheck(_Base):
    """``/api/app/version-check`` — whether the reported client is current."""

    new_version_available: bool = Field(default=False, alias="newVersionAvailable")
    force_update_required: bool = Field(default=False, alias="forceUpdateRequired")
    update_message: str | None = Field(default=None, alias="updateMessage")
    update_message_with_link: str | None = Field(default=None, alias="updateMessageWithLink")
    update_message_link: str | None = Field(default=None, alias="updateMessageLink")


class VenueSearchResult(NearestVenue):
    """One entry in a ``/api/venues`` result set."""


class VenueSearchResponse(_Base):
    """``/api/venues``.

    A non-empty ``error_code`` with HTTP 200 is a normal "no results, here's
    why" response — check it before treating an empty list as an outage.
    """

    status: int | None = None
    results: Annotated[list[VenueSearchResult], BeforeValidator(_coerce_list)] = Field(default_factory=list)
    #: Display labels for an ambiguous search, e.g. a town name matching several
    #: places ("NEWPORT (GWENT)", "NEWPORT (ISLE OF WIGHT)"). Plain strings, not
    #: objects — the app renders them into a disambiguation list verbatim.
    potential_locations: list[str] | None = Field(default=None, alias="potentialLocations")
    error_code: str | None = Field(default=None, alias="errorCode")
    error_information: str | None = Field(default=None, alias="errorInformation")
    start_date_offset_months: int | None = Field(default=None, alias="startDateOffsetMonths")
    max_search_distance: ApiFloat = Field(default=None, alias="maxSearchDistance")
    furthest_venue_distance: ApiFloat = Field(default=None, alias="furthestVenueDistance")
    nearest_plasma_venue: NearestVenue | None = Field(default=None, alias="nearestPlasmaVenue")
    count: int | None = None


class Slot(_Base):
    """One bookable slot from ``/api/appointments/{sessionId}/slots``.

    ``time`` is the value to pass back as ``sessionTime`` when booking, in the
    ``THHMM`` form appointments use.
    """

    time: str | None = None
    procedure_code: str | None = Field(default=None, alias="procedureCode")
    procedure_type: str | None = Field(default=None, alias="procedureType")
    procedure_description: str | None = Field(default=None, alias="procedureDescription")
    last_one_available: bool = Field(default=False, alias="lastOneAvailable")

    @property
    def starts_at(self) -> _Clock | None:
        """Slot start as a ``time``, or ``None`` if the wire value is malformed."""
        return parse_wire_time(self.time)


class SessionSlots(_Base):
    """``/api/appointments/{sessionId}/slots``.

    ``clashing_appointments`` is why a slot list can look bookable but fail:
    the donor already has something in the same window.
    """

    slots: Annotated[list[Slot], BeforeValidator(_coerce_list)] = Field(default_factory=list)
    clashing_appointments: Annotated[list[Appointment], BeforeValidator(_coerce_list)] = Field(
        default_factory=list, alias="clashingAppointments"
    )
