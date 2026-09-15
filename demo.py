#!/usr/bin/env python3
"""Read-only demo against a real account.

Credentials come from ``.env`` or the environment. Nothing here writes, so it is
safe to run repeatedly:

    cp .env.example .env && $EDITOR .env
    poetry run python demo.py
"""

from __future__ import annotations

import asyncio
import os
import sys

import aiohttp

from nhs_give_blood import GiveBloodClient, GiveBloodError


def _rule(title: str) -> None:
    """Print a section heading."""
    print(f"\n\033[1m{title}\033[0m")


async def main() -> int:
    """Log in and print a summary of the account."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    email = os.environ.get("NHS_GIVE_BLOOD_EMAIL")
    password = os.environ.get("NHS_GIVE_BLOOD_PASSWORD")
    if not email or not password:
        print("Set NHS_GIVE_BLOOD_EMAIL and NHS_GIVE_BLOOD_PASSWORD (see .env.example).", file=sys.stderr)
        return 2

    async with aiohttp.ClientSession() as session:
        client = GiveBloodClient(session, username=email, password=password)
        try:
            snapshot = await client.async_get_snapshot()
        except GiveBloodError as err:
            print(f"error: {err}", file=sys.stderr)
            return 1

    account = snapshot.account

    _rule("Donor")
    print(f"  Blood group      {account.blood_group}")
    print(f"  Credits          {account.donation_credit}")
    print(f"  Registered       {account.registration_date:%d %b %Y}" if account.registration_date else "")
    print(f"  Donation type    {account.procedure_description or '—'}")

    _rule("Eligibility")
    print(f"  Can donate from  {account.can_donate_from:%a %d %b %Y}" if account.can_donate_from else "  unknown")
    if account.eligibility and account.eligibility.next_possible_appointment_date:
        print(f"  Can book from    {account.eligibility.next_possible_appointment_date:%a %d %b %Y}")

    _rule("Awards")
    if awards := snapshot.awards_data:
        print(f"  Level            {awards.award_state}")
        if nxt := awards.next_award:
            print(f"  Next             {nxt.title} — {awards.credits_to_next_award} credit(s) to go")

    _rule(f"Upcoming appointments ({len(snapshot.upcoming_appointments)})")
    for appointment in snapshot.upcoming_appointments:
        venue = appointment.venue.display_name if appointment.venue else "unknown venue"
        print(f"  {appointment.starts_at:%a %d %b %Y %H:%M}  {appointment.procedure_description or '?':10}  {venue}")
    if not snapshot.upcoming_appointments:
        print("  none")

    if snapshot.donations:
        _rule(f"Recent donations (showing {len(snapshot.donations.donation)})")
        for donation in sorted(
            snapshot.donations.donation,
            key=lambda d: d.donated_at.timestamp() if d.donated_at else 0,
            reverse=True,
        )[:5]:
            when = f"{donation.donated_at:%d %b %Y}" if donation.donated_at else "unknown date"
            print(f"  {when}  {donation.venue_name or '—'}")

    if snapshot.messages:
        relevant = snapshot.messages.for_blood_group(account.blood_group)
        _rule(f"Messages for {account.blood_group} ({len(relevant)})")
        for message in relevant:
            print(f"  {message.title}")

    if snapshot.failover and snapshot.failover.is_active:
        _rule("Booking system")
        print(f"  \033[33m{snapshot.failover.header}\033[0m")
        print(f"  {snapshot.failover.content}")

    if snapshot.partial:
        _rule("Degraded")
        print(f"  These endpoints failed: {', '.join(snapshot.degraded)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
