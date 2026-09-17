#!/usr/bin/env python3
"""Read-only web server for the NHS Give Blood client.

Serves a small dashboard (``index.html``, ``index.css``, ``index.js``) and the
JSON API behind it: your upcoming appointments, and what is bookable at the
centres you use — both the ones on your account and the ones you have donated
at before.

Credentials are resolved in this order, first match winning:

1. ``--email`` / ``--password``
2. the process environment
3. a ``.env`` file (``--env-file``, default ``.env`` in the working directory)

Run it:

    poetry run python examples/server/main.py --env-file ../.env
    # then open http://127.0.0.1:8080

Endpoints: ``/api/summary``, ``/api/appointments``, ``/api/centres``,
``/api/availability``, ``/api/calendar``, ``/api/slots``, ``/healthz``. Add
``?refresh=1`` to bypass the cache.

The calendar shows every session in the month you are looking at, per centre. Which
of them are *bookable* is decided by the API's own fields — the later of the
clinical deferral date and the next possible appointment date, minus days you
already hold an appointment — and that filter can be turned off in the UI. Clicking
a period loads its real times; clicking a time copies a reference and opens the
official booking site, because this server deliberately cannot book.

This is an example, not a hardened service. It binds the loopback interface,
keeps your donor session in process memory, and never calls a write endpoint —
there is no way to book, reschedule or cancel from here.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

from nhs_give_blood import (
    PROCEDURE_CODE_PLASMA,
    PROCEDURE_CODE_PLATELET,
    PROCEDURE_CODE_WHOLE_BLOOD,
    AccountDetails,
    Appointment,
    DonationHistory,
    GiveBloodClient,
    GiveBloodError,
    Session,
    SessionSlots,
)

_LOGGER = logging.getLogger("give_blood_server")

#: Where the front-end files live — this directory.
ASSET_DIR = Path(__file__).resolve().parent

#: The only files served, by name. A whitelist rather than a static route so the
#: server cannot be talked into serving its own source.
ASSETS: dict[str, str] = {
    "index.html": "text/html",
    "index.css": "text/css",
    "index.js": "text/javascript",
    "format.js": "text/javascript",
}

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
#: Search horizon, and therefore how far the calendar can be paged. The API stops
#: returning sessions at around 77 per venue regardless of a wider range, which
#: works out at roughly four months.
DEFAULT_DAYS = 120
DEFAULT_CACHE_TTL = 300.0

#: A period's start/end clock, as the API writes it: bare 24-hour HHMM.
HHMM = re.compile(r"^([01]\d|2[0-3])[0-5]\d$")

#: How many sessions to show per centre before it becomes a wall of dates.
SESSIONS_SHOWN = 5

#: Credential names understood in the environment and the env file.
ENV_EMAIL = "NHS_GIVE_BLOOD_EMAIL"
ENV_PASSWORD = "NHS_GIVE_BLOOD_PASSWORD"  # noqa: S105 - an env var *name*, not a credential


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def load_env_file(path: Path) -> dict[str, str]:
    """Parse a minimal ``KEY=VALUE`` env file.

    Deliberately not python-dotenv: it is a dev-only optional dependency here,
    and the format in use is a handful of simple assignments. Quoted values are
    unquoted; blank lines and ``#`` comments are skipped.
    """
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, raw_value = line.partition("=")
        if not separator:
            continue
        values[key.strip()] = raw_value.strip().strip('"').strip("'")
    return values


def resolve_credentials(args: argparse.Namespace, env_file: dict[str, str]) -> tuple[str | None, str | None]:
    """Resolve credentials: flag, then environment, then env file."""
    email = args.email or os.environ.get(ENV_EMAIL) or env_file.get(ENV_EMAIL)
    password = args.password or os.environ.get(ENV_PASSWORD) or env_file.get(ENV_PASSWORD)
    return email, password


# ---------------------------------------------------------------------------
# Domain -> JSON
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Centre:
    """A venue worth checking availability at, and why it is on the list."""

    venue_id: str
    name: str
    #: ``preferred`` (on the account), ``previous`` (donated there), or both.
    source: str
    postcode: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API."""
        return {
            "venue_id": self.venue_id,
            "name": self.name,
            "source": self.source,
            "postcode": self.postcode,
        }


def collect_centres(account: AccountDetails, history: DonationHistory | None) -> list[Centre]:
    """Merge preferred and previous centres into one ordered list.

    There is no dedicated endpoint for either list: preferred centres come from
    ``accountDetails.venues``, and previous ones from the venues attached to
    donation history, with ``lastDonatedVenueId`` as a fallback when the history
    read degraded. Preferred centres come first so the UI reads as
    "mine, then where I've been".
    """
    centres: dict[str, Centre] = {}

    for venue in account.venues:
        if not venue.venue_id:
            continue
        centres[venue.venue_id] = Centre(
            venue_id=venue.venue_id,
            name=venue.venue_name or venue.venue_id,
            source="preferred",
            postcode=venue.address.postcode if venue.address else None,
        )

    for donation in history.donation if history else ():
        venue = donation.session.venue if donation.session else None
        if venue is None or not venue.venue_id:
            continue
        existing = centres.get(venue.venue_id)
        if existing is not None:
            if existing.source == "previous":
                existing.source = "preferred+previous"
            continue
        centres[venue.venue_id] = Centre(
            venue_id=venue.venue_id,
            name=venue.display_name,
            source="previous",
            postcode=venue.address.postcode if venue.address else None,
        )

    last_id = account.last_donated_venue_id
    if last_id and last_id not in centres:
        # No name available — the history read that would have supplied one is
        # either truncated or degraded. The id is still enough to ask for slots.
        centres[last_id] = Centre(venue_id=last_id, name=last_id, source="previous")

    return list(centres.values())


def appointment_payload(appointment: Appointment) -> dict[str, Any]:
    """Serialise one appointment."""
    venue = appointment.venue
    return {
        "starts_at": appointment.starts_at.isoformat() if appointment.starts_at else None,
        "procedure": appointment.procedure_description or appointment.procedure_code,
        "venue_id": venue.venue_id if venue else None,
        "venue_name": venue.display_name if venue else None,
        "session_id": appointment.session_id,
        "status": appointment.status,
    }


def session_payload(session: Session) -> dict[str, Any]:
    """Serialise one clinic session, keeping "undisclosed" distinct from zero.

    ``freeSlots`` of -1 means the API declined to say, which is not the same as
    a full session — ``total_free_slots`` already maps it to ``None``.
    """
    return {
        "session_id": session.session_id,
        "date": session.session_date.isoformat() if session.session_date else None,
        "free_slots": session.total_free_slots,
        "status_combo": session.status_combo,
        "periods": [
            {
                "start": period.start_time,
                "end": period.end_time,
                "free_slots": period.free_slots if period.has_known_free_slots else None,
            }
            for period in session.periods
        ],
    }


def _session_date(session: Session) -> datetime:
    """Sort key for sessions, so undated ones land last rather than raising."""
    return session.session_date or datetime.max.replace(tzinfo=UTC)


@dataclass(slots=True)
class Gate:
    """Which days are worth showing, given what the donor has already booked.

    Built entirely from the API's own fields rather than a rule of our own. NHSBT
    does not publish its deferral intervals — they differ by procedure, donation
    history and sex — and ``nextPossibleAppointmentDate`` already folds in the
    booking window and any appointment already held, so re-deriving "you must wait
    N weeks since your last donation" here would be guesswork that silently
    overstates what is bookable.
    """

    earliest: date | None
    can_donate_from: date | None
    can_book_from: date | None
    booked_days: tuple[date, ...]

    def allows(self, day: date) -> bool:
        """True when a session on ``day`` is worth offering the donor."""
        if self.earliest is not None and day < self.earliest:
            return False
        return day not in self.booked_days

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API, keeping the contributing dates separate."""
        return {
            "earliest": self.earliest.isoformat() if self.earliest else None,
            "can_donate_from": self.can_donate_from.isoformat() if self.can_donate_from else None,
            "can_book_from": self.can_book_from.isoformat() if self.can_book_from else None,
            "booked_days": [day.isoformat() for day in self.booked_days],
        }


def build_gate(account: AccountDetails, appointments: list[Appointment]) -> Gate:
    """Derive the gate from the account and the donor's upcoming appointments.

    ``appointments`` is expected to be ``DonorSnapshot.upcoming_appointments`` —
    de-duplicated, unstartable and cancelled entries already removed.
    """
    can_donate_from = account.can_donate_from.date() if account.can_donate_from else None
    bookable_from = account.eligibility.next_possible_appointment_date if account.eligibility else None
    can_book_from = bookable_from.date() if bookable_from else None
    limits = [day for day in (can_donate_from, can_book_from) if day is not None]
    booked_days = sorted({a.starts_at.date() for a in appointments if a.starts_at is not None})
    return Gate(
        earliest=max(limits) if limits else None,
        can_donate_from=can_donate_from,
        can_book_from=can_book_from,
        booked_days=tuple(booked_days),
    )


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------


class Dashboard:
    """Reads the API for the UI, with a short cache to spare the NHSBT rate limit."""

    def __init__(
        self,
        client: GiveBloodClient,
        *,
        procedure_code: str = PROCEDURE_CODE_WHOLE_BLOOD,
        days: int = DEFAULT_DAYS,
        cache_ttl: float = DEFAULT_CACHE_TTL,
    ) -> None:
        self._client = client
        self._procedure_code = procedure_code
        self._days = days
        self._cache_ttl = cache_ttl
        self._cache: dict[str, tuple[float, Any]] = {}

    async def _cached(self, key: str, loader: Callable[[], Awaitable[Any]], *, refresh: bool = False) -> Any:
        """Return a cached value, or load and store a fresh one.

        The timestamp is taken before the load so a slow request cannot extend
        the entry's life past its TTL.
        """
        started = time.monotonic()
        if not refresh:
            cached = self._cache.get(key)
            if cached is not None and started - cached[0] < self._cache_ttl:
                return cached[1]
        value = await loader()
        self._cache[key] = (started, value)
        return value

    @property
    def window(self) -> tuple[date, date]:
        """The availability search window: today through ``--days`` ahead."""
        start = date.today()
        return start, start + timedelta(days=self._days)

    async def snapshot(self, *, refresh: bool = False) -> Any:
        """The donor snapshot — one pass for account, appointments and history."""
        return await self._cached("snapshot", self._client.async_get_snapshot, refresh=refresh)

    async def summary(self, *, refresh: bool = False) -> dict[str, Any]:
        """Donor, eligibility and the state of the booking system."""
        snapshot = await self.snapshot(refresh=refresh)
        account: AccountDetails = snapshot.account
        awards = snapshot.awards_data
        next_appointment = snapshot.next_appointment
        eligibility = account.eligibility
        return {
            "donor": {
                "name": account.full_name,
                "blood_group": account.blood_group,
                "donation_credit": account.donation_credit,
                "award_state": awards.award_state if awards else None,
            },
            "eligibility": {
                "can_donate_from": account.can_donate_from.isoformat() if account.can_donate_from else None,
                "can_book_from": (
                    eligibility.next_possible_appointment_date.isoformat()
                    if eligibility and eligibility.next_possible_appointment_date
                    else None
                ),
                "next_appointment": appointment_payload(next_appointment) if next_appointment else None,
            },
            "failover": {
                "active": snapshot.failover.is_active if snapshot.failover else False,
                "header": snapshot.failover.header if snapshot.failover else None,
                "content": snapshot.failover.content if snapshot.failover else None,
            },
            "degraded": list(snapshot.degraded),
        }

    async def appointments(self, *, refresh: bool = False) -> dict[str, Any]:
        """Upcoming appointments, soonest first."""
        snapshot = await self.snapshot(refresh=refresh)
        appointments = snapshot.upcoming_appointments
        return {
            "appointments": [appointment_payload(a) for a in appointments],
            "count": len(appointments),
        }

    async def centres(self, *, refresh: bool = False) -> dict[str, Any]:
        """The preferred and previous centres the availability check will cover."""
        snapshot = await self.snapshot(refresh=refresh)
        centres = collect_centres(snapshot.account, snapshot.donations)
        return {
            "centres": [centre.to_dict() for centre in centres],
            "preferred": [c.venue_id for c in centres if "preferred" in c.source],
            "previous": [c.venue_id for c in centres if "previous" in c.source],
        }

    @property
    def procedure_code(self) -> str:
        """The procedure availability is being checked for."""
        return self._procedure_code

    def clamp_window(self, start: date | None, end: date | None) -> tuple[date, date]:
        """Clamp a requested range to the horizon we are willing to search."""
        today = date.today()
        limit = today + timedelta(days=self._days)
        return max(start or today, today), min(end or limit, limit)

    async def _centre_sessions(
        self,
        centre: Centre,
        start: date,
        end: date,
        *,
        refresh: bool = False,
    ) -> tuple[list[Session], str | None]:
        """Dated sessions at one centre in a window.

        A failure is returned rather than raised: one unreachable venue should not
        blank a view covering several.
        """

        async def load() -> list[Session]:
            return await self._client.async_get_sessions_at_venue(
                centre.venue_id,
                procedure_code=self._procedure_code,
                start_date=start,
                end_date=end,
            )

        key = f"sessions:{self._procedure_code}:{start.isoformat()}:{end.isoformat()}:{centre.venue_id}"
        try:
            sessions = await self._cached(key, load, refresh=refresh)
        except GiveBloodError as err:
            _LOGGER.warning("Sessions failed for %s (%s): %s", centre.name, centre.venue_id, err)
            return [], str(err)
        return [session for session in sessions if session.session_date is not None], None

    async def availability(self, *, refresh: bool = False) -> dict[str, Any]:
        """Availability across every centre, fetched concurrently.

        A per-centre summary — the calendar is the detail view.
        """
        snapshot = await self.snapshot(refresh=refresh)
        centres = collect_centres(snapshot.account, snapshot.donations)
        start, end = self.window
        results = await asyncio.gather(*(self._centre_sessions(c, start, end, refresh=refresh) for c in centres))

        payloads: list[dict[str, Any]] = []
        for centre, (sessions, error) in zip(centres, results, strict=True):
            bookable = sorted(sessions, key=_session_date)
            disclosed = [s.total_free_slots for s in bookable if s.total_free_slots is not None]
            payloads.append(
                {
                    **centre.to_dict(),
                    "error": error,
                    "next_session": bookable[0].session_date.isoformat() if bookable else None,
                    "session_count": len(bookable),
                    "free_slots": sum(disclosed) if disclosed else None,
                    "sessions": [session_payload(session) for session in bookable[:SESSIONS_SHOWN]],
                }
            )
        return {
            "procedure_code": self._procedure_code,
            "window": {"start": start.isoformat(), "end": end.isoformat()},
            "generated_at": datetime.now(UTC).isoformat(),
            "centres": payloads,
            "degraded": [payload["venue_id"] for payload in payloads if payload["error"]],
        }

    async def calendar(
        self,
        *,
        start: date | None = None,
        end: date | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """Every session in a window, grouped by day and marked bookable or not.

        Sessions are returned regardless of the gate and flagged instead, so the
        UI can offer "show everything" without another round trip.
        """
        snapshot = await self.snapshot(refresh=refresh)
        centres = collect_centres(snapshot.account, snapshot.donations)
        gate = build_gate(snapshot.account, snapshot.upcoming_appointments)
        limit = date.today() + timedelta(days=self._days)
        window_start, window_end = self.clamp_window(start, end)

        payload: dict[str, Any] = {
            "procedure_code": self._procedure_code,
            "generated_at": datetime.now(UTC).isoformat(),
            "gate": gate.to_dict(),
            "centres": [centre.to_dict() for centre in centres],
            "limit": limit.isoformat(),
            "window": {
                "start": window_start.isoformat(),
                "end": window_end.isoformat(),
            },
            "days": [],
            "degraded": [],
        }
        if window_end < window_start:
            # Asked to search entirely past the horizon: nothing to fetch.
            return {**payload, "outside_window": True}

        results = await asyncio.gather(
            *(self._centre_sessions(c, window_start, window_end, refresh=refresh) for c in centres)
        )

        days: dict[str, list[dict[str, Any]]] = {}
        degraded: list[str] = []
        for centre, (sessions, error) in zip(centres, results, strict=True):
            if error is not None:
                degraded.append(centre.venue_id)
            for session in sessions:
                day = session.session_date.date()  # _centre_sessions drops undated sessions
                days.setdefault(day.isoformat(), []).append(
                    {
                        **session_payload(session),
                        "venue_id": centre.venue_id,
                        "venue_name": centre.name,
                        "source": centre.source,
                        "bookable": gate.allows(day),
                    }
                )

        return {
            **payload,
            "outside_window": False,
            "days": [
                {"date": day, "sessions": sorted(entries, key=lambda entry: entry["venue_name"])}
                for day, entries in sorted(days.items())
            ],
            "degraded": degraded,
        }

    async def slots(
        self,
        session_id: str,
        *,
        session_date: date,
        start: str,
        end: str,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """The individual bookable times inside one period of a session.

        Fetched only when asked for: it is one request per period, and the whole
        calendar can be drawn from session-level counts alone.
        """

        async def load() -> SessionSlots:
            return await self._client.async_get_session_slots(
                session_id,
                session_date=session_date,
                start_time=start,
                end_time=end,
                procedure_code=self._procedure_code,
            )

        key = f"slots:{self._procedure_code}:{session_id}:{session_date.isoformat()}:{start}:{end}"
        result = await self._cached(key, load, refresh=refresh)
        return {
            "session_id": session_id,
            "date": session_date.isoformat(),
            "period": {"start": start, "end": end},
            "slots": [
                {
                    "time": slot.time,
                    "clock": slot.starts_at.strftime("%H:%M") if slot.starts_at else None,
                    "procedure": slot.procedure_description,
                    "last_one_available": slot.last_one_available,
                }
                for slot in result.slots
            ],
            "clashing_appointments": [appointment_payload(a) for a in result.clashing_appointments],
        }


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _wants_refresh(request: web.Request) -> bool:
    """True when the caller asked to bypass the cache."""
    return request.query.get("refresh", "").lower() in {"1", "true", "yes"}


def _dashboard(request: web.Request) -> Dashboard:
    """Fetch the dashboard off the application state."""
    dashboard: Dashboard = request.app["dashboard"]
    return dashboard


@web.middleware
async def error_middleware(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    """Turn API failures into JSON instead of an HTML traceback page."""
    try:
        return await handler(request)
    except GiveBloodError as err:
        _LOGGER.warning("%s %s failed: %s", request.method, request.path, err)
        return web.json_response({"error": str(err), "type": type(err).__name__}, status=502)


async def asset(request: web.Request) -> web.Response:
    """Serve one of the front-end files.

    Read per request rather than cached in memory: they are a few kilobytes, and
    editing the CSS or JS then only needs a browser refresh, not a restart.
    """
    name = request.match_info.get("name", "index.html")
    if name not in ASSETS:
        raise web.HTTPNotFound()
    return web.Response(
        body=(ASSET_DIR / name).read_bytes(),
        content_type=ASSETS[name],
        charset="utf-8",
        headers={"Cache-Control": "no-store"},
    )


async def api_summary(request: web.Request) -> web.Response:
    """Donor summary."""
    return web.json_response(await _dashboard(request).summary(refresh=_wants_refresh(request)))


async def api_appointments(request: web.Request) -> web.Response:
    """Upcoming appointments."""
    return web.json_response(await _dashboard(request).appointments(refresh=_wants_refresh(request)))


async def api_centres(request: web.Request) -> web.Response:
    """Centres that availability is checked at."""
    return web.json_response(await _dashboard(request).centres(refresh=_wants_refresh(request)))


async def api_availability(request: web.Request) -> web.Response:
    """Bookable sessions across the donor's centres."""
    return web.json_response(await _dashboard(request).availability(refresh=_wants_refresh(request)))


async def api_calendar(request: web.Request) -> web.Response:
    """Every session in a date range, grouped by day."""
    dashboard = _dashboard(request)
    try:
        start = date.fromisoformat(request.query["start"]) if "start" in request.query else None
        end = date.fromisoformat(request.query["end"]) if "end" in request.query else None
    except ValueError:
        return web.json_response({"error": "start and end must be YYYY-MM-DD"}, status=400)
    return web.json_response(await dashboard.calendar(start=start, end=end, refresh=_wants_refresh(request)))


async def api_slots(request: web.Request) -> web.Response:
    """The individual times inside one session period."""
    query = request.query
    session_id = query.get("session_id", "")
    start = query.get("start", "")
    end = query.get("end", "")
    if not session_id or not HHMM.match(start) or not HHMM.match(end):
        return web.json_response({"error": "session_id, date, start and end are required (times as HHMM)"}, status=400)
    try:
        session_date = date.fromisoformat(query.get("date", ""))
    except ValueError:
        return web.json_response({"error": "date must be YYYY-MM-DD"}, status=400)
    return web.json_response(
        await _dashboard(request).slots(
            session_id,
            session_date=session_date,
            start=start,
            end=end,
            refresh=_wants_refresh(request),
        )
    )


async def healthz(request: web.Request) -> web.Response:
    """Liveness probe — deliberately does not touch the upstream API."""
    return web.json_response({"status": "ok"})


def build_app(dashboard: Dashboard) -> web.Application:
    """Wire the routes up to a dashboard."""
    app = web.Application(middlewares=[error_middleware])
    app["dashboard"] = dashboard
    app.add_routes(
        [
            web.get("/", asset),
            web.get("/static/{name}", asset),
            web.get("/api/summary", api_summary),
            web.get("/api/appointments", api_appointments),
            web.get("/api/centres", api_centres),
            web.get("/api/availability", api_availability),
            web.get("/api/calendar", api_calendar),
            web.get("/api/slots", api_slots),
            web.get("/healthz", healthz),
        ]
    )
    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Assemble the argument parser."""
    parser = argparse.ArgumentParser(
        prog="examples/server/main.py",
        description=__doc__.split("\n")[0],
    )
    parser.add_argument("--email", help=f"Donor email (default: ${ENV_EMAIL}, then the env file)")
    parser.add_argument("--password", help=f"Donor password (default: ${ENV_PASSWORD}, then the env file)")
    parser.add_argument("--env-file", default=".env", help="Env file to read credentials from (default: .env)")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Interface to bind (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port to bind (default: {DEFAULT_PORT})")
    parser.add_argument(
        "--procedure",
        default=PROCEDURE_CODE_WHOLE_BLOOD,
        choices=[PROCEDURE_CODE_WHOLE_BLOOD, PROCEDURE_CODE_PLASMA, PROCEDURE_CODE_PLATELET],
        help="Procedure to check availability for (default: WB)",
    )
    parser.add_argument(
        "--days", type=int, default=DEFAULT_DAYS, help=f"Days ahead to search (default: {DEFAULT_DAYS})"
    )
    parser.add_argument(
        "--cache-ttl",
        type=float,
        default=DEFAULT_CACHE_TTL,
        help=f"Seconds to cache each read (default: {DEFAULT_CACHE_TTL:g})",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    return parser


async def serve(args: argparse.Namespace, email: str, password: str) -> int:
    """Log in, then serve until interrupted."""
    async with aiohttp.ClientSession() as session:
        client = GiveBloodClient(session, username=email, password=password)
        await client.async_ensure_authenticated()
        _LOGGER.info("Authenticated as donor %s", client.donor_id or "(unknown)")

        dashboard = Dashboard(
            client,
            procedure_code=args.procedure,
            days=args.days,
            cache_ttl=args.cache_ttl,
        )
        runner = web.AppRunner(build_app(dashboard))
        await runner.setup()
        site = web.TCPSite(runner, args.host, args.port)
        await site.start()
        _LOGGER.info("Serving on http://%s:%s/ — press Ctrl-C to stop", args.host, args.port)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    env_path = Path(args.env_file)
    env_file: dict[str, str] = {}
    if env_path.is_file():
        try:
            env_file = load_env_file(env_path)
        except OSError as err:
            print(f"error: could not read {env_path}: {err}", file=sys.stderr)
            return 2

    email, password = resolve_credentials(args, env_file)
    if not email or not password:
        print(
            f"No credentials. Pass --email/--password, set {ENV_EMAIL}/{ENV_PASSWORD}, "
            f"or put them in {env_path} (see .env.example).",
            file=sys.stderr,
        )
        return 2

    try:
        return asyncio.run(serve(args, email, password))
    except KeyboardInterrupt:
        return 0
    except GiveBloodError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
