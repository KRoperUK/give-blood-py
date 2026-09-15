"""Command line interface for the NHS Give Blood client.

Read commands run unattended. Write commands (``book``, ``cancel``,
``reschedule``) change a real NHS appointment and refuse to run without
``--yes``.

Credentials come from the environment (``NHS_GIVE_BLOOD_EMAIL`` /
``NHS_GIVE_BLOOD_PASSWORD``, optionally via a ``.env``) or ``--email`` /
``--password``. Prefer the environment: shell history is not a secret store.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from typing import Any

import aiohttp

from .client import GiveBloodClient
from .const import PROCEDURE_CODE_WHOLE_BLOOD
from .exceptions import GiveBloodError
from .models import Appointment

_LOGGER = logging.getLogger("nhs_give_blood.cli")


def _load_dotenv() -> None:
    """Load a local .env when python-dotenv is installed. Optional dependency."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dev-only convenience
        return
    load_dotenv()


def _credentials(args: argparse.Namespace) -> tuple[str | None, str | None]:
    """Resolve credentials from flags then environment."""
    email = args.email or os.environ.get("NHS_GIVE_BLOOD_EMAIL")
    password = args.password or os.environ.get("NHS_GIVE_BLOOD_PASSWORD")
    return email, password


def _fmt_dt(value: datetime | None) -> str:
    """Render a datetime for terminal output."""
    return value.strftime("%a %d %b %Y %H:%M") if value else "—"


def _describe(appointment: Appointment) -> str:
    """One-line appointment summary."""
    venue = appointment.venue.display_name if appointment.venue else "unknown venue"
    return f"{_fmt_dt(appointment.starts_at)}  {appointment.procedure_description or '?':10}  {venue}"


async def _cmd_whoami(client: GiveBloodClient, args: argparse.Namespace) -> int:
    """Print account summary."""
    account = await client.async_get_account_details()
    awards = account.awards_data
    print(f"Donor            {account.donor_id}")
    print(f"Blood group      {account.blood_group}")
    print(f"Credits          {account.donation_credit}")
    print(f"Award level      {awards.award_state if awards else '—'}")
    if awards and (nxt := awards.next_award):
        print(f"Next award       {nxt.title} ({awards.credits_to_next_award} credit(s) to go)")
    print(f"Can donate from  {_fmt_dt(account.can_donate_from)}")
    print(
        f"Can book from    {_fmt_dt(account.eligibility.next_possible_appointment_date if account.eligibility else None)}"
    )
    print(f"Registered       {_fmt_dt(account.registration_date)}")
    return 0


async def _cmd_appointments(client: GiveBloodClient, args: argparse.Namespace) -> int:
    """List upcoming appointments."""
    appointments = await client.async_get_future_appointments()
    if not appointments:
        print("No upcoming appointments.")
        return 0
    for appointment in sorted(appointments, key=lambda a: a.starts_at or datetime.max):
        print(_describe(appointment))
    return 0


async def _cmd_history(client: GiveBloodClient, args: argparse.Namespace) -> int:
    """List donation history."""
    history = await client.async_get_donation_history()
    for donation in sorted(history.donation, key=lambda d: d.donated_at or datetime.min, reverse=True):
        print(f"{_fmt_dt(donation.donated_at)}  type={donation.type or '?'}  {donation.venue_name or '—'}")
    if history.has_further_donations:
        print("\n(API truncated the list — more donations exist than shown.)")
    return 0


async def _cmd_snapshot(client: GiveBloodClient, args: argparse.Namespace) -> int:
    """Print the full snapshot as JSON."""
    snapshot = await client.async_get_snapshot()
    payload: dict[str, Any] = {
        "account": snapshot.account.model_dump(mode="json", by_alias=False),
        "appointments": [a.model_dump(mode="json") for a in snapshot.upcoming_appointments],
        "awards": snapshot.awards_data.model_dump(mode="json") if snapshot.awards_data else None,
        "features": snapshot.features.model_dump(mode="json") if snapshot.features else None,
        "failover": snapshot.failover.model_dump(mode="json") if snapshot.failover else None,
        "degraded": list(snapshot.degraded),
    }
    print(json.dumps(payload, indent=2, default=str))
    return 0


async def _cmd_venues(client: GiveBloodClient, args: argparse.Namespace) -> int:
    """Search venues near a postcode or place."""
    response = await client.async_search_venues(
        args.search,
        procedure_code=args.procedure,
        include_fully_booked_sessions=args.include_full,
    )
    if response.error_code:
        print(f"Search returned {response.error_code}: {response.error_information or '(no detail)'}")
    for result in response.results:
        venue = result.venue
        distance = f"{result.venue_distance:.1f} mi" if result.venue_distance is not None else "—"
        print(
            f"{venue.venue_id if venue else '?':8} {distance:>8}  "
            f"next {_fmt_dt(result.date_of_next_session):24}  "
            f"{venue.display_name if venue else '?'}"
        )
    return 0


async def _cmd_sessions(client: GiveBloodClient, args: argparse.Namespace) -> int:
    """List sessions at a venue."""
    sessions = await client.async_get_sessions_at_venue(args.venue_id, procedure_code=args.procedure)
    for session in sessions:
        free = session.total_free_slots
        print(
            f"{session.session_id:8} {_fmt_dt(session.session_date):24} "
            f"combo={session.status_combo}  free={free if free is not None else 'undisclosed'}"
        )
    return 0


async def _cmd_cancel(client: GiveBloodClient, args: argparse.Namespace) -> int:
    """Cancel an appointment. Requires --yes."""
    if not args.yes:
        print("Refusing to cancel without --yes. This releases a real NHS clinic slot.", file=sys.stderr)
        return 2
    await client.async_cancel_appointment(args.appointment_id)
    print(f"Cancelled {args.appointment_id}.")
    return 0


async def _cmd_book(client: GiveBloodClient, args: argparse.Namespace) -> int:
    """Book an appointment. Requires --yes."""
    if not args.yes:
        print("Refusing to book without --yes. This creates a real NHS clinic appointment.", file=sys.stderr)
        return 2
    result = await client.async_book_appointment(
        session_id=args.session_id,
        session_date=args.date,
        session_time=args.time,
        venue_id=args.venue_id,
        procedure_code=args.procedure,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """Assemble the argument parser."""
    parser = argparse.ArgumentParser(prog="give-blood", description=__doc__.split("\n")[0])
    parser.add_argument("--email", help="Donor email (default: $NHS_GIVE_BLOOD_EMAIL)")
    parser.add_argument("--password", help="Donor password (default: $NHS_GIVE_BLOOD_PASSWORD)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("whoami", help="Account and eligibility summary").set_defaults(func=_cmd_whoami)
    sub.add_parser("appointments", help="Upcoming appointments").set_defaults(func=_cmd_appointments)
    sub.add_parser("history", help="Donation history").set_defaults(func=_cmd_history)
    sub.add_parser("snapshot", help="Everything, as JSON").set_defaults(func=_cmd_snapshot)

    venues = sub.add_parser("venues", help="Search venues")
    venues.add_argument("search", help="Postcode or place name")
    venues.add_argument("--procedure", default=PROCEDURE_CODE_WHOLE_BLOOD)
    venues.add_argument("--include-full", action="store_true", help="Include fully booked sessions")
    venues.set_defaults(func=_cmd_venues)

    sessions = sub.add_parser("sessions", help="List sessions at a venue")
    sessions.add_argument("venue_id")
    sessions.add_argument("--procedure", default=PROCEDURE_CODE_WHOLE_BLOOD)
    sessions.set_defaults(func=_cmd_sessions)

    book = sub.add_parser("book", help="Book an appointment (requires --yes)")
    book.add_argument("session_id")
    book.add_argument("--date", required=True, help="Session date, YYYY-MM-DD")
    book.add_argument("--time", required=True, help="Slot time, THHMM")
    book.add_argument("--venue-id", required=True)
    book.add_argument("--procedure", default=PROCEDURE_CODE_WHOLE_BLOOD)
    book.add_argument("--yes", action="store_true", help="Confirm this real-world booking")
    book.set_defaults(func=_cmd_book)

    cancel = sub.add_parser("cancel", help="Cancel an appointment (requires --yes)")
    cancel.add_argument("appointment_id")
    cancel.add_argument("--yes", action="store_true", help="Confirm this real-world cancellation")
    cancel.set_defaults(func=_cmd_cancel)

    return parser


async def _run(args: argparse.Namespace) -> int:
    """Open a session, build a client, dispatch the subcommand."""
    email, password = _credentials(args)
    if not email or not password:
        print(
            "No credentials. Set NHS_GIVE_BLOOD_EMAIL and NHS_GIVE_BLOOD_PASSWORD, or pass --email/--password.",
            file=sys.stderr,
        )
        return 2
    async with aiohttp.ClientSession() as session:
        client = GiveBloodClient(session, username=email, password=password)
        try:
            await client.async_ensure_authenticated()
            return int(await args.func(client, args))
        except GiveBloodError as err:
            print(f"error: {err}", file=sys.stderr)
            return 1


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``give-blood`` console script."""
    _load_dotenv()
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
