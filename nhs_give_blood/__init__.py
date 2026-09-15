"""Async Python client for the NHS Give Blood (NHSBT) donor API.

This talks to the private API behind the official NHS Give Blood mobile app. It
is an unofficial client: NHSBT publishes no contract for these endpoints and may
change them without notice.

Quick start::

    import aiohttp
    from nhs_give_blood import GiveBloodClient

    async with aiohttp.ClientSession() as session:
        client = GiveBloodClient(session, username="you@example.invalid", password="…")
        snapshot = await client.async_get_snapshot()
        print(snapshot.account.blood_group, snapshot.account.donation_credit)
        if appointment := snapshot.next_appointment:
            print(appointment.starts_at, appointment.venue.display_name if appointment.venue else "?")

Two behaviours worth knowing before you build on this:

* **The session is caller-owned.** The client never creates or closes an
  ``aiohttp.ClientSession``.
* **Dates are venue-local.** Every datetime returned is timezone-aware in
  Europe/London; the wire format is naive and uses ``0001-01-01T00:00:00`` as a
  null sentinel, both normalised away by the models.
"""

from __future__ import annotations

from .auth import AuthManager, TokenBundle, TokenListener, decode_jwt_claims
from .client import DonorSnapshot, GiveBloodClient
from .const import (
    APP_API_KEY,
    AWARD_TIERS,
    BASE_URL,
    BLOOD_GROUPS,
    DEFAULT_CLIENT_VERSION,
    NULL_DATETIME,
    PROCEDURE_CODE_PLASMA,
    PROCEDURE_CODE_PLATELET,
    PROCEDURE_CODE_WHOLE_BLOOD,
)
from .exceptions import (
    GiveBloodApiError,
    GiveBloodAuthError,
    GiveBloodBookingError,
    GiveBloodConnectionError,
    GiveBloodError,
    GiveBloodInvalidCredentialsError,
    GiveBloodRateLimitError,
    GiveBloodTokenExpiredError,
)
from .models import (
    AccountDetails,
    Address,
    Appointment,
    Award,
    AwardsData,
    Donation,
    DonationHistory,
    DonorMessage,
    Eligibility,
    FailoverBanner,
    FeatureFlags,
    LoginResponse,
    MessageBundle,
    NearestVenue,
    Period,
    Serology,
    Session,
    SessionSlots,
    VenueSearchResponse,
    VenueSearchResult,
    VenueSummary,
    VersionCheck,
    parse_api_datetime,
    parse_wire_time,
)

__all__ = [
    "APP_API_KEY",
    "AWARD_TIERS",
    "BASE_URL",
    "BLOOD_GROUPS",
    "DEFAULT_CLIENT_VERSION",
    "NULL_DATETIME",
    "PROCEDURE_CODE_PLASMA",
    "PROCEDURE_CODE_PLATELET",
    "PROCEDURE_CODE_WHOLE_BLOOD",
    "AccountDetails",
    "Address",
    "Appointment",
    "AuthManager",
    "Award",
    "AwardsData",
    "Donation",
    "DonationHistory",
    "DonorMessage",
    "DonorSnapshot",
    "Eligibility",
    "FailoverBanner",
    "FeatureFlags",
    "GiveBloodApiError",
    "GiveBloodAuthError",
    "GiveBloodBookingError",
    "GiveBloodClient",
    "GiveBloodConnectionError",
    "GiveBloodError",
    "GiveBloodInvalidCredentialsError",
    "GiveBloodRateLimitError",
    "GiveBloodTokenExpiredError",
    "LoginResponse",
    "MessageBundle",
    "NearestVenue",
    "Period",
    "Serology",
    "Session",
    "SessionSlots",
    "TokenBundle",
    "TokenListener",
    "VenueSearchResponse",
    "VenueSearchResult",
    "VenueSummary",
    "VersionCheck",
    "decode_jwt_claims",
    "parse_api_datetime",
    "parse_wire_time",
]
