"""Live smoke tests against a real NHS Give Blood account.

Deselected by default. Run them with::

    pytest -m live

Credentials come from ``NHS_GIVE_BLOOD_EMAIL`` and ``NHS_GIVE_BLOOD_PASSWORD``, in the
environment or a ``.env`` found by searching upward. Without them every test skips, so
a fork or an unconfigured checkout sees a green no-op rather than a failure.

**Why these exist.** Every other test in this suite mocks HTTP with ``aresponses``, so
the whole suite would stay green through a complete change in NHSBT's wire format.
This is a client for a private, undocumented API that can change without notice; these
tests are the only thing that would notice before a user did.

**What they assert.** Shapes and invariants, never values. A donor's credits and
appointment dates change, so hard-coding them would break constantly and risk
committing personal data. The most valuable assertions here are the ones that catch a
wire-format change: that every datetime is timezone-aware, that fields the models
declare have not silently vanished, and that ``model_extra`` is empty for payloads we
claim to model fully — that last one is the early warning that NHSBT *added* something.

**Read-only, enforced.** Nothing here books, reschedules or cancels. Doing so from a
test — let alone a scheduled CI job — would occupy or release a real slot at an NHS
donation centre. :class:`TestReadOnlyByConstruction` asserts that this module contains
no reference to a write method, so the guarantee does not rest on nobody adding one.
"""

from __future__ import annotations

import os
import pathlib
import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import aiohttp
import pytest

from nhs_give_blood import (
    AccountDetails,
    GiveBloodClient,
    TokenBundle,
)

pytestmark = pytest.mark.live


class Redacted:
    """Holds a live payload without exposing it in a pytest traceback.

    pytest renders fixture arguments in the failure header, so a module-scoped
    snapshot fixture printed the donor's real name, id and address on any failure —
    into CI logs, which are separate from the repository the PII guard protects.
    Tests unwrap via :attr:`value`; the repr says nothing.
    """

    __slots__ = ("value",)

    def __init__(self, value: Any) -> None:
        self.value = value

    def __repr__(self) -> str:
        return f"<Redacted {type(self.value).__name__} — withheld from test output>"


#: Client methods that change a real appointment. Never called from this module.
WRITE_METHODS = (
    "async_book_appointment",
    "async_reschedule_appointment",
    "async_cancel_appointment",
)


def _credentials() -> tuple[str, str] | None:
    """Load credentials from the environment or a .env searched upward."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dev convenience only
        pass
    else:
        load_dotenv()
    email = os.environ.get("NHS_GIVE_BLOOD_EMAIL")
    password = os.environ.get("NHS_GIVE_BLOOD_PASSWORD")
    return (email, password) if email and password else None


@pytest.fixture(scope="module")
def credentials() -> tuple[str, str]:
    """Real credentials, or skip the whole module."""
    found = _credentials()
    if found is None:
        pytest.skip("no NHS_GIVE_BLOOD_EMAIL / NHS_GIVE_BLOOD_PASSWORD available")
    return found


@pytest.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    """A real aiohttp session."""
    async with aiohttp.ClientSession() as client_session:
        yield client_session


@pytest.fixture
async def client(session: aiohttp.ClientSession, credentials: tuple[str, str]) -> GiveBloodClient:
    """An authenticated client against the real API."""
    email, password = credentials
    instance = GiveBloodClient(session, username=email, password=password)
    await instance.async_ensure_authenticated()
    return instance


@pytest.fixture(scope="module")
async def donor(credentials: tuple[str, str]) -> Redacted:
    """One snapshot shared by the read tests, wrapped so tracebacks stay clean.

    Tests take this wrapper and unwrap with ``donor.value``. Handing them the
    snapshot directly is what leaks: pytest renders a test's own arguments in the
    failure header, so the payload would print in full on any failure.

    Module-scoped deliberately: a login per test would be several password
    authentications per run against an account-lockout policy, for no extra signal.
    """
    email, password = credentials
    async with aiohttp.ClientSession() as session:
        instance = GiveBloodClient(session, username=email, password=password)
        return Redacted(await instance.async_get_snapshot())


class TestReadOnlyByConstruction:
    """The read-only guarantee must not rest on nobody adding a write call."""

    @pytest.mark.parametrize("method", WRITE_METHODS)
    def test_this_module_never_calls_a_write_method(self, method: str) -> None:
        source = pathlib.Path(__file__).read_text()
        # The names appear once each in WRITE_METHODS itself; anything more is a call.
        occurrences = len(re.findall(rf"\b{method}\b", source))
        assert occurrences == 1, (
            f"{method} is referenced {occurrences} times in the live tests. "
            "Live tests must never change a real NHS appointment."
        )


class TestAuthentication:
    """The auth ladder, against the real service."""

    async def test_password_login_returns_the_account(
        self, session: aiohttp.ClientSession, credentials: tuple[str, str]
    ) -> None:
        """Login embeds the account payload, so no follow-up read is needed."""
        email, password = credentials
        instance = GiveBloodClient(session, username=email, password=password)

        account = await instance.async_login()

        assert account is not None
        assert account.donor_id
        assert account.blood_group

    async def test_access_token_carries_the_expected_claims(self, client: GiveBloodClient) -> None:
        """donor_id is what consumers key accounts on; can_book gates booking."""
        from nhs_give_blood import decode_jwt_claims

        claims = decode_jwt_claims(client.export_tokens().access_token)

        assert claims.get("donor_id"), "no donor_id claim — consumers key on this"
        assert isinstance(claims.get("exp"), (int, float)), "no exp claim to derive expiry from"
        assert isinstance(claims.get("can_book"), bool)

    async def test_expiry_is_derived_and_in_the_future(self, client: GiveBloodClient) -> None:
        """The API sends no expires_in, so expiry comes from the JWT itself."""
        import time

        tokens = client.export_tokens()

        assert tokens.expires_at > time.time()
        assert tokens.is_fresh

    async def test_refresh_rotates_the_refresh_token(
        self, session: aiohttp.ClientSession, credentials: tuple[str, str]
    ) -> None:
        """Rotation is what keeps an unattended consumer alive.

        Note what does *not* change: the service reissues the **same** access token
        while the current one is still valid, and rotates only the refresh token. So
        asserting the access token changed would fail against the real API — which is
        exactly the sort of wrong assumption these tests exist to catch.
        """
        email, password = credentials
        instance = GiveBloodClient(session, username=email, password=password)
        await instance.async_ensure_authenticated()
        original = instance.export_tokens()
        assert original.refresh_token

        refreshed = await instance.auth.refresh()

        assert refreshed.access_token, "refresh must always yield a usable access token"
        assert refreshed.refresh_token != original.refresh_token, "refresh token should rotate"
        assert refreshed.expires_at > 0
        assert refreshed.is_fresh

    async def test_a_token_only_client_can_read(self, session: aiohttp.ClientSession, client: GiveBloodClient) -> None:
        """Confirms the persisted bundle is sufficient on its own after a restart."""
        bundle = TokenBundle.from_mapping(client.export_tokens().as_dict())
        token_only = GiveBloodClient(session, token_bundle=bundle)

        account = await token_only.async_get_account_details()

        assert account.donor_id

    async def test_validate_accepts_a_live_token(self, client: GiveBloodClient) -> None:
        assert await client.async_validate_token() is True


class TestSnapshot:
    """The aggregate read every consumer uses."""

    async def test_nothing_is_degraded_on_a_healthy_run(self, donor: Redacted) -> None:
        """A degraded endpoint here is the signal that something upstream moved."""
        snapshot_once = donor.value
        assert not snapshot_once.partial, f"degraded endpoints: {snapshot_once.degraded}"

    async def test_the_supplementary_reads_all_arrived(self, donor: Redacted) -> None:
        snapshot_once = donor.value
        assert snapshot_once.messages is not None
        assert snapshot_once.features is not None
        assert snapshot_once.failover is not None
        assert snapshot_once.donations is not None

    async def test_account_carries_the_fields_consumers_depend_on(self, donor: Redacted) -> None:
        snapshot_once = donor.value
        account = snapshot_once.account

        assert account.donor_id
        assert account.blood_group
        assert isinstance(account.donation_credit, int)
        assert account.eligibility is not None
        assert account.awards_data is not None

    async def test_every_datetime_is_timezone_aware(self, donor: Redacted) -> None:
        """A naive datetime reaching a consumer is read as UTC and shifts the time.

        The wire format is naive and venue-local, so this is the assertion that
        catches the localisation breaking.
        """
        snapshot_once = donor.value
        naive: list[str] = []

        def check(value: Any, path: str) -> None:
            if isinstance(value, datetime):
                if value.tzinfo is None:
                    naive.append(path)
            elif isinstance(value, dict):
                for key, item in value.items():
                    check(item, f"{path}.{key}")
            elif isinstance(value, (list, tuple)):
                for index, item in enumerate(value):
                    check(item, f"{path}[{index}]")
            elif hasattr(value, "model_dump"):
                check(value.model_dump(), path)

        check(snapshot_once.account, "account")
        for index, appointment in enumerate(snapshot_once.upcoming_appointments):
            check(appointment, f"appointments[{index}]")

        assert not naive, f"naive datetimes: {naive}"

    async def test_the_null_date_sentinel_never_reaches_a_consumer(self, donor: Redacted) -> None:
        """0001-01-01T00:00:00 is .NET's DateTime.MinValue, used instead of null."""
        snapshot_once = donor.value
        rendered = snapshot_once.account.model_dump_json()

        assert "0001-01-01" not in rendered


class TestSchemaDrift:
    """Early warning that NHSBT changed the payload.

    These are the tests worth having. A field appearing is how you learn about a new
    feature; a field vanishing is how you learn something broke.
    """

    async def test_account_has_no_unmodelled_fields(self, donor: Redacted) -> None:
        """Fails when the API grows a field, which is the point.

        Not a bug in itself — the model uses extra="allow" precisely so a new field
        cannot break parsing. This test is the prompt to decide whether to model it.
        """
        snapshot_once = donor.value
        extra = sorted(snapshot_once.account.model_extra or {})

        assert not extra, (
            f"the account payload has unmodelled fields: {extra}. Decide whether to model them, then update this test."
        )

    async def test_appointments_have_no_unmodelled_fields(self, donor: Redacted) -> None:
        snapshot_once = donor.value
        drift: dict[str, list[str]] = {}
        for index, appointment in enumerate(snapshot_once.upcoming_appointments):
            if extra := sorted(appointment.model_extra or {}):
                drift[f"appointments[{index}]"] = extra
            if appointment.session and (extra := sorted(appointment.session.model_extra or {})):
                drift[f"appointments[{index}].session"] = extra

        assert not drift, f"unmodelled appointment fields: {drift}"

    async def test_feature_flags_have_no_unmodelled_fields(self, donor: Redacted) -> None:
        """New flags are how NHSBT signals a new capability."""
        snapshot_once = donor.value
        assert snapshot_once.features is not None
        extra = sorted(snapshot_once.features.model_extra or {})

        assert not extra, f"new feature flags: {extra}"


class TestReadEndpoints:
    """Each endpoint individually, so a failure names the one that moved."""

    async def test_account_details(self, client: GiveBloodClient) -> None:
        account = await client.async_get_account_details()
        assert isinstance(account, AccountDetails)
        assert account.donor_id

    async def test_future_appointments(self, client: GiveBloodClient) -> None:
        """May legitimately be empty; the shape is what matters."""
        appointments = await client.async_get_future_appointments()

        assert isinstance(appointments, list)
        for appointment in appointments:
            assert appointment.starts_at is None or appointment.starts_at.tzinfo is not None

    async def test_donation_history(self, client: GiveBloodClient) -> None:
        history = await client.async_get_donation_history()

        assert isinstance(history.has_further_donations, bool)
        for donation in history.donation:
            assert donation.donated_at is None or donation.donated_at.tzinfo is not None

    async def test_awards(self, client: GiveBloodClient) -> None:
        awards = await client.async_get_awards()

        assert awards.awards, "a registered donor should have an award ladder"
        assert isinstance(awards.total_credits, int)
        if awards.next_award is not None:
            assert awards.credits_to_next_award is not None

    async def test_messages_are_filterable(self, client: GiveBloodClient) -> None:
        """The API returns every blood group's messages; filtering is the client's job."""
        bundle = await client.async_get_messages()
        account = await client.async_get_account_details()

        relevant = bundle.for_blood_group(account.blood_group)

        assert len(relevant) <= len(bundle.donor_messages)

    async def test_feature_flags(self, client: GiveBloodClient) -> None:
        account = await client.async_get_account_details()

        features = await client.async_get_feature_flags(account.blood_group)

        assert isinstance(features.check_in, bool)

    async def test_failover_banner(self, client: GiveBloodClient) -> None:
        """is_active is the signal that writes would fail."""
        banner = await client.async_get_failover()

        assert isinstance(banner.is_active, bool)

    async def test_version_check(self, client: GiveBloodClient) -> None:
        """Flags when the client version we impersonate stops being accepted."""
        result = await client.async_get_version_check()

        assert isinstance(result.force_update_required, bool)
        assert not result.force_update_required, (
            "NHSBT now requires a newer app version; DEFAULT_CLIENT_VERSION needs bumping"
        )


class TestVenueSearch:
    """The search paths, which take real parameters."""

    async def test_search_by_postcode(self, client: GiveBloodClient) -> None:
        account = await client.async_get_account_details()
        home = account.home_address
        if home is None or not home.postcode:
            pytest.skip("account has no home postcode to search near")

        response = await client.async_search_venues(home.postcode)

        # A 200 with an error code is the API's normal "no sessions in range".
        assert response.error_code or response.results is not None
        for result in response.results:
            assert result.venue is not None
            assert result.date_of_next_session is None or result.date_of_next_session.tzinfo

    async def test_sessions_at_a_known_venue(self, client: GiveBloodClient) -> None:
        account = await client.async_get_account_details()
        venue_id = account.last_donated_venue_id
        if not venue_id:
            pytest.skip("account has no last-donated venue")

        sessions = await client.async_get_sessions_at_venue(venue_id)

        assert isinstance(sessions, list)
        for entry in sessions:
            assert entry.session_id
            assert len(entry.status_combo) == 4, "the status combo is a 4-character key"
            # -1 means undisclosed, and must not be read as zero.
            for period in entry.periods:
                if period.free_slots == -1:
                    assert not period.has_known_free_slots
