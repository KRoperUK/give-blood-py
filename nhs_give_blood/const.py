"""Constants, endpoints and domain semantics for the NHS Give Blood app API.

Everything here was recovered from the public NHS Give Blood Android app
(``com.savant.mobile.nhs.nhsgiveblood`` 4.9.1) and verified against the live
API. See ``docs/api-reference.md`` for the derivation.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

BASE_URL: Final = "https://my.blood.co.uk"

# The app ships a single static client key in its axios request interceptor and
# sends it on every call to the API host. It identifies the *app*, not a user:
# it carries no user authentication and grants nothing on its own — the donor
# Bearer token does all the authorisation. It is embedded in a public APK, so
# it is not a secret and there is no configuration mechanism to supply it
# through; hardcoding it is the only way the client can function.
APP_API_KEY: Final = "b0046936-5a05-439e-8a89-5beab70829b7"

#: Version the client reports as ``Nhsbt-Client-Version``. Some responses
#: (feature flags, failover banner, version check) vary with it.
DEFAULT_CLIENT_VERSION: Final = "4.9.1"

#: ``Nhsbt-Client-Type``. The web front end sends ``web``; the mobile app
#: sends ``app`` and gets the app feature-flag set.
CLIENT_TYPE_APP: Final = "app"
CLIENT_TYPE_WEB: Final = "web"

DEFAULT_TIMEOUT: Final = 30.0
DEFAULT_MAX_RETRIES: Final = 3
DEFAULT_RETRY_BASE_DELAY: Final = 0.5
DEFAULT_RETRY_MAX_DELAY: Final = 8.0

#: Statuses worth retrying. 401 is excluded on purpose — it is handled by the
#: token-refresh ladder in ``auth.py``, not by the retry loop.
RETRYABLE_STATUS: Final = frozenset({429, 500, 502, 503, 504})

#: Access tokens are ~30-minute RS256 JWTs. Refresh this many seconds early so
#: an in-flight request can't expire between the check and the server receiving
#: it.
TOKEN_EXPIRY_MARGIN: Final = 60.0

#: The API signals "your access token has aged out, refresh it" with HTTP 401
#: *and* this code in the JSON body. A 401 without it means the credentials or
#: refresh token are genuinely dead and re-authentication is required.
TOKEN_EXPIRED_CODE: Final = "TOKEN_EXPIRED"  # noqa: S105 - a wire status code, not a credential

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

EP_LOGIN: Final = "/api/auth/v2/login"
EP_LOGOUT: Final = "/api/auth/logout"
EP_REFRESH: Final = "/api/auth/refresh"
EP_VALIDATE: Final = "/api/auth/validate"
EP_CHANGE_PASSWORD: Final = "/api/auth/change-password"  # noqa: S105 - URL path, not a credential
EP_RESET_PASSWORD: Final = "/api/auth/reset/password"  # noqa: S105 - URL path, not a credential
EP_REQUEST_USERNAME: Final = "/api/auth/request/username"
EP_REQUEST_PASSWORD_RESET: Final = "/api/auth/request/password-reset"  # noqa: S105 - URL path

EP_ACCOUNT_DETAILS: Final = "/api/account/v2/details"
EP_DONATION_HISTORY: Final = "/api/account/donation-history"
EP_AWARDS: Final = "/api/account/awards"

EP_APPOINTMENTS_FUTURE: Final = "/api/appointments/future"
EP_APPOINTMENT: Final = "/api/appointments/{appointment_id}"
EP_APPOINTMENT_BOOK: Final = "/api/appointments/book"
EP_APPOINTMENT_REPLACE: Final = "/api/appointments/replace"
EP_SESSION_SLOTS: Final = "/api/appointments/{session_id}/slots"

EP_VENUES: Final = "/api/venues"
EP_ADDRESS_SEARCH: Final = "/api/address-search"
EP_SESSIONS_AT_VENUE: Final = "/api/sessions/{venue_id}"
EP_SESSIONS_PAR: Final = "/api/sessions/par"

EP_MESSAGES: Final = "/api/messages"
EP_FEATURES: Final = "/api/features"
EP_FEATURES_FAILOVER: Final = "/api/features/failover"
EP_VERSION_CHECK: Final = "/api/app/version-check/{platform}/{version}"

# ---------------------------------------------------------------------------
# Domain semantics
# ---------------------------------------------------------------------------

#: The API returns .NET ``DateTime.MinValue`` instead of null for "no date".
#: Treat it as ``None`` everywhere — see ``models.parse_api_datetime``.
NULL_DATETIME: Final = "0001-01-01T00:00:00"

#: ``periods[].freeSlots`` is a *string* and uses -1 to mean "not disclosed"
#: rather than "zero free slots".
FREE_SLOTS_UNKNOWN: Final = -1

#: Appointment ``time`` fields are 24-hour ``THHMM``; session ``periods`` use
#: bare ``HHMM``. Both are venue-local (Europe/London).
APPOINTMENT_TIME_PREFIX: Final = "T"
VENUE_TIMEZONE: Final = "Europe/London"

#: Procedure codes accepted by the booking and search endpoints. ``procedureCode``
#: on an *appointment* uses PLT for platelets, while the *donor's* registered
#: procedureCode uses PL1 — they are different vocabularies, so never compare
#: one to the other.
PROCEDURE_CODE_WHOLE_BLOOD: Final = "WB"
PROCEDURE_CODE_PLASMA: Final = "PLS"
PROCEDURE_CODE_PLATELET: Final = "PLT"

#: Award tiers in ascending order. ``awardsData.awardState`` is one of these,
#: or "None" before the first tier is reached.
AWARD_TIERS: Final = ("Bronze", "Silver", "Gold", "Emerald", "Ruby")

#: Blood groups the API reports, plus the "All" wildcard used by targeted
#: donor messages.
BLOOD_GROUPS: Final = ("A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-")
BLOOD_GROUP_ALL: Final = "All"
