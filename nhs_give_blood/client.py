"""Async HTTP client for the NHS Give Blood app API."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import aiohttp

from .auth import AuthManager, TokenBundle, TokenListener, build_headers
from .const import (
    BASE_URL,
    CLIENT_TYPE_APP,
    CLIENT_TYPE_WEB,
    DEFAULT_CLIENT_VERSION,
    DEFAULT_MAX_RETRIES,
    DEFAULT_RETRY_BASE_DELAY,
    DEFAULT_RETRY_MAX_DELAY,
    DEFAULT_TIMEOUT,
    EP_ACCOUNT_DETAILS,
    EP_APPOINTMENT,
    EP_APPOINTMENT_BOOK,
    EP_APPOINTMENT_REPLACE,
    EP_APPOINTMENTS_FUTURE,
    EP_AWARDS,
    EP_DONATION_HISTORY,
    EP_FEATURES,
    EP_FEATURES_FAILOVER,
    EP_MESSAGES,
    EP_SESSION_SLOTS,
    EP_SESSIONS_AT_VENUE,
    EP_VALIDATE,
    EP_VENUES,
    EP_VERSION_CHECK,
    PROCEDURE_CODE_WHOLE_BLOOD,
    RETRYABLE_STATUS,
    TOKEN_EXPIRED_CODE,
)
from .exceptions import (
    GiveBloodApiError,
    GiveBloodAuthError,
    GiveBloodBookingError,
    GiveBloodConnectionError,
    GiveBloodRateLimitError,
    classify_auth_error,
    error_summary,
)
from .models import (
    AccountDetails,
    Appointment,
    AwardsData,
    DonationHistory,
    FailoverBanner,
    FeatureFlags,
    MessageBundle,
    Session,
    SessionSlots,
    VenueSearchResponse,
    VersionCheck,
)

_LOGGER = logging.getLogger(__name__)

__all__ = ["DonorSnapshot", "GiveBloodClient"]

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True, slots=True)
class DonorSnapshot:
    """One consistent read of everything a dashboard needs.

    Produced by :meth:`GiveBloodClient.async_get_snapshot`. The optional fields
    degrade independently: a failed feature-flag call leaves ``features`` as
    ``None`` rather than failing the whole snapshot, because none of the
    supplementary endpoints are worth losing the account payload over. Check
    :attr:`partial` to tell a clean read from a degraded one.
    """

    account: AccountDetails
    appointments: list[Appointment] = field(default_factory=list)
    donations: DonationHistory | None = None
    awards: AwardsData | None = None
    messages: MessageBundle | None = None
    features: FeatureFlags | None = None
    failover: FailoverBanner | None = None
    #: Endpoint names that failed during this snapshot.
    degraded: tuple[str, ...] = ()

    @property
    def partial(self) -> bool:
        """True when at least one supplementary endpoint failed."""
        return bool(self.degraded)

    @property
    def next_appointment(self) -> Appointment | None:
        """Soonest upcoming appointment across both sources.

        ``/api/appointments/future`` and the account payload's ``appointments``
        normally agree, but they are separate reads and can disagree briefly
        after a booking, so both are considered.
        """
        candidates = [
            appointment
            for appointment in (*self.appointments, *self.account.appointments)
            if appointment.starts_at is not None and not appointment.is_cancelled
        ]
        if not candidates:
            return None
        deduped: dict[tuple[str | None, str | None], Appointment] = {}
        for appointment in candidates:
            deduped.setdefault((appointment.session_id, appointment.time), appointment)
        return min(deduped.values(), key=lambda a: a.starts_at or datetime.max)

    @property
    def upcoming_appointments(self) -> list[Appointment]:
        """All upcoming appointments, de-duplicated and sorted by start time."""
        deduped: dict[tuple[str | None, str | None], Appointment] = {}
        for appointment in (*self.appointments, *self.account.appointments):
            if appointment.starts_at is None or appointment.is_cancelled:
                continue
            deduped.setdefault((appointment.session_id, appointment.time), appointment)
        return sorted(deduped.values(), key=lambda a: a.starts_at or datetime.max)

    @property
    def awards_data(self) -> AwardsData | None:
        """Awards from the dedicated endpoint, else the account payload's copy."""
        return self.awards or self.account.awards_data


def _fmt_date(value: date | datetime | str | None) -> str | None:
    """Render a date in the API's ``YYYY-MM-DDT00:00:00`` search format."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        value = value.date()
    return f"{value.isoformat()}T00:00:00"


def _clean_params(params: dict[str, Any]) -> dict[str, str]:
    """Drop ``None`` values and render bools the way the API expects.

    ASP.NET model binding rejects Python's ``True``/``False`` capitalisation, so
    booleans must be lowercased.
    """
    cleaned: dict[str, str] = {}
    for key, value in params.items():
        if value is None:
            continue
        cleaned[key] = "true" if value is True else "false" if value is False else str(value)
    return cleaned


class GiveBloodClient:
    """Client for the NHS Give Blood donor API.

    The aiohttp session is **caller-owned**: this class never creates or closes
    one, so a host application (Home Assistant) can share its own.

    Authentication accepts either credentials, a stored :class:`TokenBundle`, or
    both. With both, a stale bundle silently falls back to a password login,
    which is what makes unattended long-running use work across token rotation.

    Args:
        session: An open ``aiohttp.ClientSession``.
        username: Donor email address.
        password: Donor password.
        token_bundle: Previously persisted tokens.
        base_url: Override the API host (tests, staging).
        client_version: Value sent as ``Nhsbt-Client-Version``.
        timeout: Total per-request timeout in seconds.
        max_retries: Attempts for retryable statuses on safe methods.
        on_token_update: Callback invoked whenever tokens rotate.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        username: str | None = None,
        password: str | None = None,
        token_bundle: TokenBundle | None = None,
        base_url: str = BASE_URL,
        client_version: str = DEFAULT_CLIENT_VERSION,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_base_delay: float = DEFAULT_RETRY_BASE_DELAY,
        retry_max_delay: float = DEFAULT_RETRY_MAX_DELAY,
        on_token_update: TokenListener | None = None,
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._client_version = client_version
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._max_retries = max(1, max_retries)
        self._retry_base_delay = retry_base_delay
        self._retry_max_delay = retry_max_delay
        self._auth = AuthManager(
            session,
            username=username,
            password=password,
            token_bundle=token_bundle,
            base_url=base_url,
            client_version=client_version,
            timeout=timeout,
            on_token_update=on_token_update,
        )
        # Serialises token acquisition so a burst of concurrent calls triggers
        # one login/refresh rather than one per call.
        self._auth_lock = asyncio.Lock()

    # -- auth passthrough --------------------------------------------------

    @property
    def auth(self) -> AuthManager:
        """The underlying token manager."""
        return self._auth

    @property
    def is_authenticated(self) -> bool:
        """True when a fresh access token is held."""
        return self._auth.is_authenticated

    @property
    def donor_id(self) -> str | None:
        """Donor identifier from the access token, if one is held."""
        return self._auth.donor_id

    def export_tokens(self) -> TokenBundle:
        """Snapshot the current tokens for persistence."""
        return self._auth.export_tokens()

    def apply_tokens(self, bundle: TokenBundle) -> None:
        """Load previously persisted tokens."""
        self._auth.apply_tokens(bundle)

    async def async_login(self) -> AccountDetails | None:
        """Force a password login and return the account payload it carries.

        Cheaper than login-then-fetch: the login response already embeds the
        full account details.
        """
        return (await self._auth.login()).account_details

    async def async_ensure_authenticated(self) -> None:
        """Obtain a token if one isn't already held. Raises on failure."""
        async with self._auth_lock:
            await self._auth.async_get_access_token()

    async def async_logout(self) -> None:
        """Sign out and clear tokens."""
        await self._auth.logout()

    # -- transport ---------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        authenticated: bool = True,
        client_type: str = CLIENT_TYPE_APP,
        retry_non_idempotent: bool = False,
    ) -> Any:
        """Perform a request, handling auth, retries and error mapping.

        Retries cover only transient statuses and, by default, only safe
        methods: replaying a booking POST after an ambiguous 500 could create a
        duplicate appointment.
        """
        url = f"{self._base_url}{path}"
        may_retry = method.upper() in _SAFE_METHODS or retry_non_idempotent
        attempts = self._max_retries if may_retry else 1
        refreshed = False
        last_error: Exception | None = None

        for attempt in range(attempts):
            token: str | None = None
            if authenticated:
                async with self._auth_lock:
                    token = await self._auth.async_get_access_token()
            headers = build_headers(
                client_version=self._client_version,
                client_type=client_type,
                access_token=token,
            )
            try:
                async with self._session.request(
                    method,
                    url,
                    params=_clean_params(params) if params else None,
                    json=json_body,
                    headers=headers,
                    timeout=self._timeout,
                ) as response:
                    body = await _decode_body(response)

                    if response.status < 400:
                        return body

                    if response.status == 401 and authenticated and not refreshed:
                        code = body.get("code") if isinstance(body, dict) else None
                        if code == TOKEN_EXPIRED_CODE or code is None:
                            # Mirrors the app's interceptor: refresh once, then
                            # replay. Only once, so a server that always 401s
                            # can't spin.
                            refreshed = True
                            async with self._auth_lock:
                                await self._auth.refresh()
                            continue
                        raise classify_auth_error(response.status, body)

                    if response.status in (401, 403):
                        raise classify_auth_error(response.status, body)

                    if response.status in RETRYABLE_STATUS and attempt < attempts - 1:
                        delay = self._backoff(attempt, response.headers.get("Retry-After"))
                        _LOGGER.debug(
                            "%s %s returned %s; retrying in %.1fs (attempt %s/%s)",
                            method,
                            path,
                            response.status,
                            delay,
                            attempt + 1,
                            attempts,
                        )
                        await asyncio.sleep(delay)
                        continue

                    raise _map_http_error(method, path, response.status, body, response.headers.get("Retry-After"))

            except (aiohttp.ClientError, TimeoutError) as err:
                last_error = err
                if attempt < attempts - 1 and may_retry:
                    delay = self._backoff(attempt, None)
                    _LOGGER.debug("%s %s failed (%s); retrying in %.1fs", method, path, err, delay)
                    await asyncio.sleep(delay)
                    continue
                raise GiveBloodConnectionError(f"{method} {path} failed: {err}") from err

        # Only reachable if the loop exhausted its attempts on 401-refresh or
        # retryable statuses without a terminal raise.
        raise GiveBloodConnectionError(f"{method} {path} failed after {attempts} attempts: {last_error}")

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        """Exponential backoff with jitter, honouring ``Retry-After``."""
        if retry_after:
            try:
                return min(float(retry_after), self._retry_max_delay)
            except ValueError:
                pass
        delay: float = min(self._retry_base_delay * (2**attempt), self._retry_max_delay)
        # Jitter avoids a fleet of Home Assistant instances retrying in lockstep.
        # Not a security decision, so the stdlib PRNG is the right tool.
        jitter: float = 0.5 + random.random() / 2  # noqa: S311  # nosec B311
        return delay * jitter

    # -- read endpoints ----------------------------------------------------

    async def async_get_account_details(self) -> AccountDetails:
        """Fetch the donor account payload."""
        return AccountDetails.model_validate(await self._request("GET", EP_ACCOUNT_DETAILS))

    async def async_get_future_appointments(self) -> list[Appointment]:
        """Fetch upcoming appointments."""
        body = await self._request("GET", EP_APPOINTMENTS_FUTURE)
        if not isinstance(body, list):
            return []
        return [Appointment.model_validate(item) for item in body]

    async def async_get_donation_history(self) -> DonationHistory:
        """Fetch donation history.

        The API truncates long histories — check
        ``DonationHistory.has_further_donations``.
        """
        return DonationHistory.model_validate(await self._request("GET", EP_DONATION_HISTORY))

    async def async_get_awards(self) -> AwardsData:
        """Fetch award/milestone state."""
        return AwardsData.model_validate(await self._request("GET", EP_AWARDS))

    async def async_get_messages(self) -> MessageBundle:
        """Fetch in-app donor messages.

        The API nests these under a ``messages`` key and returns messages for
        *all* blood groups; filter with ``MessageBundle.for_blood_group``.
        """
        body = await self._request("GET", EP_MESSAGES)
        payload = body.get("messages", body) if isinstance(body, dict) else {}
        return MessageBundle.model_validate(payload)

    async def async_get_feature_flags(self, blood_group: str | None = None) -> FeatureFlags:
        """Fetch server-side feature switches for this client version."""
        body = await self._request(
            "GET",
            EP_FEATURES,
            params={"platform": CLIENT_TYPE_APP, "version": self._client_version, "bloodGroup": blood_group},
        )
        return FeatureFlags.model_validate(body)

    async def async_get_failover(self) -> FailoverBanner:
        """Fetch the booking-system outage banner.

        Sent as ``web`` deliberately: the app queries the failover banner with
        the web client type, and the app type returns a different (empty) body.
        """
        body = await self._request(
            "GET",
            EP_FEATURES_FAILOVER,
            params={"platform": CLIENT_TYPE_WEB, "version": self._client_version},
            client_type=CLIENT_TYPE_WEB,
        )
        return FailoverBanner.model_validate(body)

    async def async_get_version_check(self, platform: str = "android", version: str | None = None) -> VersionCheck:
        """Ask whether the reported client version is still supported."""
        path = EP_VERSION_CHECK.format(platform=platform, version=version or self._client_version)
        return VersionCheck.model_validate(await self._request("GET", path))

    async def async_validate_token(self) -> bool:
        """Check the current access token server-side.

        Returns False rather than raising when the token is simply rejected;
        connection failures still raise, because "unreachable" is not "invalid".
        """
        try:
            await self._request("GET", EP_VALIDATE)
        except GiveBloodAuthError:
            return False
        return True

    async def async_search_venues(
        self,
        search_criteria: str,
        *,
        procedure_code: str = PROCEDURE_CODE_WHOLE_BLOOD,
        procedure_type: str | None = None,
        start_date: date | datetime | str | None = None,
        end_date: date | datetime | str | None = None,
        venue_id: str | None = None,
        home_latitude: float | None = None,
        home_longitude: float | None = None,
        include_fully_booked_sessions: bool = False,
    ) -> VenueSearchResponse:
        """Search venues by postcode or place name.

        ``search_criteria`` takes whatever the app's search box takes — a
        postcode or a town. Defaults to the next 60 days when no dates are given.
        """
        today = date.today()
        return VenueSearchResponse.model_validate(
            await self._request(
                "GET",
                EP_VENUES,
                params={
                    "searchCriteria": search_criteria,
                    "startDate": _fmt_date(start_date) or _fmt_date(today),
                    "endDate": _fmt_date(end_date) or _fmt_date(today + timedelta(days=60)),
                    "procedureType": procedure_type,
                    "procedureCode": procedure_code,
                    "venueId": venue_id,
                    "homeLatitude": home_latitude,
                    "homeLongitude": home_longitude,
                    "includeFullyBookedSessions": include_fully_booked_sessions,
                },
            )
        )

    async def async_get_sessions_at_venue(
        self,
        venue_id: str,
        *,
        procedure_code: str = PROCEDURE_CODE_WHOLE_BLOOD,
        start_date: date | datetime | str | None = None,
        end_date: date | datetime | str | None = None,
        include_fully_booked_sessions: bool = False,
    ) -> list[Session]:
        """List clinic sessions at a venue. ``venue`` is null on these."""
        today = date.today()
        body = await self._request(
            "GET",
            EP_SESSIONS_AT_VENUE.format(venue_id=venue_id),
            params={
                "startDate": _fmt_date(start_date) or _fmt_date(today),
                "endDate": _fmt_date(end_date) or _fmt_date(today + timedelta(days=60)),
                "procedureCode": procedure_code,
                "includeFullyBookedSessions": include_fully_booked_sessions,
            },
        )
        sessions = body.get("sessions", []) if isinstance(body, dict) else []
        return [Session.model_validate(item) for item in sessions]

    async def async_get_session_slots(
        self,
        session_id: str,
        *,
        session_date: date | datetime | str,
        start_time: str,
        end_time: str,
        procedure_code: str = PROCEDURE_CODE_WHOLE_BLOOD,
    ) -> SessionSlots:
        """List bookable slots in one period of a session.

        ``start_time``/``end_time`` are the bare ``HHMM`` values from the
        session's ``periods`` — the API rejects a window it didn't advertise.
        """
        return SessionSlots.model_validate(
            await self._request(
                "GET",
                EP_SESSION_SLOTS.format(session_id=session_id),
                params={
                    "sessionDate": _fmt_date(session_date),
                    "startTime": start_time,
                    "endTime": end_time,
                    "procedureCode": procedure_code,
                },
            )
        )

    async def async_get_snapshot(self, *, include_donations: bool = True) -> DonorSnapshot:
        """Read everything a dashboard needs in one pass.

        The account payload is mandatory — its failure propagates. Everything
        else is fetched concurrently and degrades to ``None`` on failure, with
        the endpoint recorded in :attr:`DonorSnapshot.degraded`, so one flaky
        supplementary endpoint can't blank an entire dashboard.
        """
        account = await self.async_get_account_details()

        names: list[str] = ["appointments", "messages", "features", "failover"]
        tasks: list[Any] = [
            self.async_get_future_appointments(),
            self.async_get_messages(),
            self.async_get_feature_flags(account.blood_group),
            self.async_get_failover(),
        ]
        if include_donations:
            names.append("donations")
            tasks.append(self.async_get_donation_history())

        results = await asyncio.gather(*tasks, return_exceptions=True)
        values: dict[str, Any] = {}
        degraded: list[str] = []
        for name, result in zip(names, results, strict=True):
            if isinstance(result, BaseException):
                _LOGGER.debug("Snapshot degraded: %s failed (%s)", name, result)
                degraded.append(name)
                continue
            values[name] = result

        return DonorSnapshot(
            account=account,
            appointments=values.get("appointments") or [],
            donations=values.get("donations"),
            awards=account.awards_data,
            messages=values.get("messages"),
            features=values.get("features"),
            failover=values.get("failover"),
            degraded=tuple(degraded),
        )

    # -- write endpoints ---------------------------------------------------
    #
    # These change a real NHS booking. They are never retried on an ambiguous
    # failure, and this library deliberately does not wrap them in any
    # convenience that could fire one unintentionally.

    async def async_book_appointment(
        self,
        *,
        session_id: str,
        session_date: date | datetime | str,
        session_time: str,
        venue_id: str,
        procedure_code: str = PROCEDURE_CODE_WHOLE_BLOOD,
    ) -> dict[str, Any]:
        """Book an appointment.

        ``session_time`` is the slot's ``THHMM`` value from
        :meth:`async_get_session_slots`.

        Warning:
            This creates a real appointment at an NHS clinic. Never call it
            speculatively — a wasted slot is a wasted donation.
        """
        body = await self._request(
            "POST",
            EP_APPOINTMENT_BOOK,
            json_body={
                "sessionID": session_id,
                "sessionDate": _fmt_date(session_date),
                "sessionTime": session_time,
                "venueId": venue_id,
                "procedureCode": procedure_code,
            },
        )
        return body if isinstance(body, dict) else {"response": body}

    async def async_reschedule_appointment(
        self,
        *,
        new_session_id: str,
        new_session_date: date | datetime | str,
        new_session_time: str,
        new_venue_id: str,
        old_session_id: str,
        old_session_date: date | datetime | str,
        old_session_time: str,
        old_venue_id: str,
        procedure_code: str = PROCEDURE_CODE_WHOLE_BLOOD,
    ) -> dict[str, Any]:
        """Move an existing appointment to a new slot.

        Atomic server-side: prefer this over cancel-then-book, which can lose
        the slot in between.
        """
        body = await self._request(
            "POST",
            EP_APPOINTMENT_REPLACE,
            json_body={
                "newAppointment": {
                    "sessionID": new_session_id,
                    "sessionDate": _fmt_date(new_session_date),
                    "sessionTime": new_session_time,
                    "venueId": new_venue_id,
                },
                "oldAppointment": {
                    "sessionID": old_session_id,
                    "sessionDate": _fmt_date(old_session_date),
                    "sessionTime": old_session_time,
                    "venueId": old_venue_id,
                },
                "procedureCode": procedure_code,
            },
        )
        return body if isinstance(body, dict) else {"response": body}

    async def async_cancel_appointment(self, appointment_id: str) -> None:
        """Cancel an appointment.

        Warning:
            Irreversible. The slot is released immediately and may be taken by
            another donor.
        """
        await self._request("DELETE", EP_APPOINTMENT.format(appointment_id=appointment_id))


def _map_http_error(method: str, path: str, status: int, body: Any, retry_after: str | None) -> GiveBloodApiError:
    """Turn a failed response into the most specific exception available."""
    summary = error_summary(body)
    if status == 429:
        try:
            parsed_retry = float(retry_after) if retry_after else None
        except ValueError:
            parsed_retry = None
        return GiveBloodRateLimitError(status, f"{method} {path} rate limited", retry_after=parsed_retry, details=body)
    if "appointment" in path:
        errors = body.get("errors") if isinstance(body, dict) else None
        return GiveBloodBookingError(
            status,
            f"{method} {path} refused: {summary}",
            validation_errors=errors if isinstance(errors, list) else None,
            details=body,
        )
    return GiveBloodApiError(status, f"{method} {path} failed: {summary}", details=body)


async def _decode_body(response: aiohttp.ClientResponse) -> Any:
    """Decode a response as JSON, falling back to text.

    Web-only routes answer with the site's HTML 404 page, so a decode failure
    is data, not an exception.
    """
    if response.status == 204:
        return None
    try:
        return await response.json(content_type=None)
    except (aiohttp.ContentTypeError, ValueError, json.JSONDecodeError):
        return await response.text()
