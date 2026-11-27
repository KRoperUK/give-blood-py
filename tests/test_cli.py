"""Unit tests for the write-path CLI commands.

``book`` and ``cancel`` change real NHS appointments, so their argument
wiring and the ``--yes`` guard are tested here with a stubbed client rather
than only by the live test.
"""

from __future__ import annotations

import argparse
from typing import Any

from nhs_give_blood import Appointment, Donation, DonationHistory
from nhs_give_blood.cli import _cmd_appointments, _cmd_book, _cmd_cancel, _cmd_history


class StubClient:
    """Records write calls instead of hitting the API."""

    def __init__(self) -> None:
        self.booked: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.appointments: list[Appointment] = []
        self.history = DonationHistory(donation=[])

    async def async_book_appointment(self, **kwargs: Any) -> dict[str, Any]:
        self.booked.append(kwargs)
        return {"ok": True}

    async def async_cancel_appointment(self, appointment_id: str) -> None:
        self.cancelled.append(appointment_id)

    async def async_get_future_appointments(self) -> list[Appointment]:
        return self.appointments

    async def async_get_donation_history(self) -> DonationHistory:
        return self.history


def _book_args(**overrides: Any) -> argparse.Namespace:
    values: dict[str, Any] = {
        "session_id": "S1",
        "date": "2026-10-08",
        "time": "T0900",
        "venue_id": "V1",
        "procedure": "WB",
        "yes": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class TestBook:
    async def test_refuses_without_yes(self) -> None:
        client = StubClient()
        assert await _cmd_book(client, _book_args(yes=False)) == 2  # type: ignore[arg-type]
        assert client.booked == []

    async def test_forwards_all_arguments(self) -> None:
        client = StubClient()
        assert await _cmd_book(client, _book_args()) == 0  # type: ignore[arg-type]
        assert client.booked == [
            {
                "session_id": "S1",
                "session_date": "2026-10-08",
                "session_time": "T0900",
                "venue_id": "V1",
                "procedure_code": "WB",
            }
        ]


class TestCancel:
    async def test_refuses_without_yes(self) -> None:
        client = StubClient()
        args = argparse.Namespace(appointment_id="A1", yes=False)
        assert await _cmd_cancel(client, args) == 2  # type: ignore[arg-type]
        assert client.cancelled == []

    async def test_forwards_the_appointment_id(self) -> None:
        client = StubClient()
        args = argparse.Namespace(appointment_id="A1", yes=True)
        assert await _cmd_cancel(client, args) == 0  # type: ignore[arg-type]
        assert client.cancelled == ["A1"]


class TestListingToleratesMissingDates:
    """Regression tests: dateless entries must sort last, not crash."""

    async def test_appointments_with_a_missing_session(self) -> None:
        client = StubClient()
        client.appointments = [
            Appointment.model_validate({"time": "T1000"}),
            Appointment.model_validate({"time": "T0900", "session": {"sessionDate": "2026-10-08T00:00:00"}}),
        ]
        assert await _cmd_appointments(client, argparse.Namespace()) == 0  # type: ignore[arg-type]

    async def test_history_with_a_missing_session(self) -> None:
        client = StubClient()
        client.history = DonationHistory(
            donation=[
                Donation.model_validate({"donationId": "B"}),
                Donation.model_validate({"donationId": "A", "session": {"sessionDate": "2024-01-01T00:00:00"}}),
            ]
        )
        assert await _cmd_history(client, argparse.Namespace()) == 0  # type: ignore[arg-type]
