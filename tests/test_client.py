"""Client transport and endpoint tests."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import aiohttp
import pytest
from aresponses import ResponsesMockServer

from nhs_give_blood import (
    GiveBloodApiError,
    GiveBloodBookingError,
    GiveBloodClient,
    GiveBloodConnectionError,
    GiveBloodInvalidCredentialsError,
    GiveBloodRateLimitError,
    GiveBloodTokenExpiredError,
    TokenBundle,
)
from nhs_give_blood.const import (
    EP_ACCOUNT_DETAILS,
    EP_APPOINTMENT_BOOK,
    EP_APPOINTMENTS_FUTURE,
    EP_AWARDS,
    EP_DONATION_HISTORY,
    EP_FEATURES,
    EP_FEATURES_FAILOVER,
    EP_MESSAGES,
    EP_REFRESH,
    EP_VALIDATE,
    EP_VENUES,
)

from .fixtures import TEST_BASE_URL, TEST_HOST, json_response, load_fixture, make_jwt


class TestHeaders:
    """The app's header set is load-bearing, not cosmetic."""

    async def test_every_request_carries_the_app_key_and_client_identity(
        self, authed_client: GiveBloodClient, aresponses: ResponsesMockServer
    ) -> None:
        from nhs_give_blood.const import APP_API_KEY

        captured: dict[str, str] = {}

        async def handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
            captured.update(request.headers)
            return json_response(load_fixture("account_details"))

        aresponses.add(TEST_HOST, EP_ACCOUNT_DETAILS, "GET", handler)
        await authed_client.async_get_account_details()

        assert captured["ApiKey"] == APP_API_KEY
        assert captured["Nhsbt-Client-Type"] == "app"
        assert captured["Nhsbt-Client-Version"]
        assert captured["Authorization"].startswith("Bearer ")
        assert captured["Cache-Control"] == "no-cache, no-store"


class TestReadEndpoints:
    """Happy paths for each read endpoint."""

    async def test_account_details(self, authed_client: GiveBloodClient, aresponses: ResponsesMockServer) -> None:
        aresponses.add(TEST_HOST, EP_ACCOUNT_DETAILS, "GET", json_response(load_fixture("account_details")))
        account = await authed_client.async_get_account_details()
        assert account.blood_group

    async def test_future_appointments(self, authed_client: GiveBloodClient, aresponses: ResponsesMockServer) -> None:
        aresponses.add(TEST_HOST, EP_APPOINTMENTS_FUTURE, "GET", json_response(load_fixture("appointments_future")))
        appointments = await authed_client.async_get_future_appointments()
        assert len(appointments) == 2

    async def test_future_appointments_tolerates_a_non_list_body(
        self, authed_client: GiveBloodClient, aresponses: ResponsesMockServer
    ) -> None:
        aresponses.add(TEST_HOST, EP_APPOINTMENTS_FUTURE, "GET", json_response({"unexpected": True}))
        assert await authed_client.async_get_future_appointments() == []

    async def test_donation_history(self, authed_client: GiveBloodClient, aresponses: ResponsesMockServer) -> None:
        aresponses.add(TEST_HOST, EP_DONATION_HISTORY, "GET", json_response(load_fixture("donation_history")))
        history = await authed_client.async_get_donation_history()
        assert history.donation

    async def test_awards(self, authed_client: GiveBloodClient, aresponses: ResponsesMockServer) -> None:
        aresponses.add(TEST_HOST, EP_AWARDS, "GET", json_response(load_fixture("awards")))
        assert (await authed_client.async_get_awards()).awards

    async def test_messages_unwraps_the_envelope(
        self, authed_client: GiveBloodClient, aresponses: ResponsesMockServer
    ) -> None:
        aresponses.add(TEST_HOST, EP_MESSAGES, "GET", json_response(load_fixture("messages")))
        bundle = await authed_client.async_get_messages()
        assert bundle.donor_messages

    async def test_feature_flags_pass_platform_and_version(
        self, authed_client: GiveBloodClient, aresponses: ResponsesMockServer
    ) -> None:
        captured: dict[str, str] = {}

        async def handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
            captured.update(request.query)
            return json_response(load_fixture("features"))

        aresponses.add(TEST_HOST, EP_FEATURES, "GET", handler, match_querystring=False)
        await authed_client.async_get_feature_flags("A-")

        assert captured["platform"] == "app"
        assert captured["bloodGroup"] == "A-"
        assert "version" in captured

    async def test_failover_is_requested_as_the_web_client(
        self, authed_client: GiveBloodClient, aresponses: ResponsesMockServer
    ) -> None:
        """The app queries failover with the web client type; app returns an empty body."""
        captured: dict[str, str] = {}

        async def handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
            captured["client_type"] = request.headers["Nhsbt-Client-Type"]
            captured["platform"] = request.query["platform"]
            return json_response(load_fixture("failover"))

        aresponses.add(TEST_HOST, EP_FEATURES_FAILOVER, "GET", handler, match_querystring=False)
        banner = await authed_client.async_get_failover()

        assert captured == {"client_type": "web", "platform": "web"}
        assert banner.is_active is False

    async def test_venue_search_sends_lowercase_booleans(
        self, authed_client: GiveBloodClient, aresponses: ResponsesMockServer
    ) -> None:
        """ASP.NET model binding rejects Python's ``True``/``False`` capitalisation."""
        captured: dict[str, str] = {}

        async def handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
            captured.update(request.query)
            return json_response(load_fixture("venues"))

        aresponses.add(TEST_HOST, EP_VENUES, "GET", handler, match_querystring=False)
        await authed_client.async_search_venues("SW1A 1AA", include_fully_booked_sessions=True)

        assert captured["includeFullyBookedSessions"] == "true"
        assert captured["startDate"].endswith("T00:00:00")

    async def test_venue_search_defaults_to_a_sixty_day_window(
        self, authed_client: GiveBloodClient, aresponses: ResponsesMockServer
    ) -> None:
        captured: dict[str, str] = {}

        async def handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
            captured.update(request.query)
            return json_response(load_fixture("venues"))

        aresponses.add(TEST_HOST, EP_VENUES, "GET", handler, match_querystring=False)
        await authed_client.async_search_venues("SW1A 1AA")

        start = date.fromisoformat(captured["startDate"].split("T")[0])
        end = date.fromisoformat(captured["endDate"].split("T")[0])
        assert (end - start).days == 60

    async def test_sessions_at_venue_unwraps_the_envelope(
        self, authed_client: GiveBloodClient, aresponses: ResponsesMockServer
    ) -> None:
        aresponses.add(
            TEST_HOST,
            "/api/sessions/TSTV1",
            "GET",
            json_response(load_fixture("sessions_at_venue")),
            match_querystring=False,
        )
        sessions = await authed_client.async_get_sessions_at_venue("TSTV1")
        assert sessions and sessions[0].session_id

    async def test_validate_returns_false_on_rejection_not_an_exception(
        self, authed_client: GiveBloodClient, aresponses: ResponsesMockServer
    ) -> None:
        aresponses.add(TEST_HOST, EP_VALIDATE, "GET", json_response({"code": "INVALID"}, status=403))
        assert await authed_client.async_validate_token() is False

    async def test_validate_returns_true_when_accepted(
        self, authed_client: GiveBloodClient, aresponses: ResponsesMockServer
    ) -> None:
        aresponses.add(TEST_HOST, EP_VALIDATE, "GET", json_response({}))
        assert await authed_client.async_validate_token() is True


class TestTokenExpiryHandling:
    """The 401-refresh-replay interceptor."""

    async def test_token_expired_triggers_refresh_and_replays_the_request(
        self, session: aiohttp.ClientSession, aresponses: ResponsesMockServer
    ) -> None:
        import time as _time

        client = GiveBloodClient(
            session,
            base_url=TEST_BASE_URL,
            token_bundle=TokenBundle(access_token=make_jwt(), refresh_token="r", expires_at=_time.time() + 3600),
        )
        aresponses.add(TEST_HOST, EP_ACCOUNT_DETAILS, "GET", json_response({"code": "TOKEN_EXPIRED"}, status=401))
        aresponses.add(TEST_HOST, EP_REFRESH, "POST", json_response({"accessToken": make_jwt()}))
        aresponses.add(TEST_HOST, EP_ACCOUNT_DETAILS, "GET", json_response(load_fixture("account_details")))

        account = await client.async_get_account_details()

        assert account.blood_group

    async def test_it_refreshes_at_most_once_per_request(
        self, session: aiohttp.ClientSession, aresponses: ResponsesMockServer
    ) -> None:
        """A server that always 401s must not put the client in a refresh loop."""
        import time as _time

        client = GiveBloodClient(
            session,
            base_url=TEST_BASE_URL,
            token_bundle=TokenBundle(access_token=make_jwt(), refresh_token="r", expires_at=_time.time() + 3600),
        )
        aresponses.add(TEST_HOST, EP_ACCOUNT_DETAILS, "GET", json_response({"code": "TOKEN_EXPIRED"}, status=401))
        aresponses.add(TEST_HOST, EP_REFRESH, "POST", json_response({"accessToken": make_jwt()}))
        aresponses.add(TEST_HOST, EP_ACCOUNT_DETAILS, "GET", json_response({"code": "TOKEN_EXPIRED"}, status=401))

        with pytest.raises(GiveBloodTokenExpiredError) as excinfo:
            await client.async_get_account_details()
        assert excinfo.value.reauth_required

    async def test_a_403_surfaces_immediately_without_a_refresh(
        self, authed_client: GiveBloodClient, aresponses: ResponsesMockServer
    ) -> None:
        aresponses.add(TEST_HOST, EP_ACCOUNT_DETAILS, "GET", json_response({}, status=403))
        with pytest.raises(GiveBloodInvalidCredentialsError):
            await authed_client.async_get_account_details()


class TestRetries:
    """Retry policy: transient statuses on safe methods only."""

    async def test_a_retryable_status_is_retried_then_succeeds(
        self, session: aiohttp.ClientSession, aresponses: ResponsesMockServer
    ) -> None:
        import time as _time

        client = GiveBloodClient(
            session,
            base_url=TEST_BASE_URL,
            token_bundle=TokenBundle(access_token=make_jwt(), expires_at=_time.time() + 3600),
            retry_base_delay=0.0,
            retry_max_delay=0.0,
        )
        aresponses.add(TEST_HOST, EP_ACCOUNT_DETAILS, "GET", json_response({}, status=503))
        aresponses.add(TEST_HOST, EP_ACCOUNT_DETAILS, "GET", json_response(load_fixture("account_details")))

        assert (await client.async_get_account_details()).blood_group

    async def test_a_non_retryable_status_fails_immediately(
        self, authed_client: GiveBloodClient, aresponses: ResponsesMockServer
    ) -> None:
        aresponses.add(TEST_HOST, EP_ACCOUNT_DETAILS, "GET", json_response({"message": "nope"}, status=404))
        with pytest.raises(GiveBloodApiError) as excinfo:
            await authed_client.async_get_account_details()
        assert excinfo.value.status == 404

    async def test_rate_limiting_surfaces_retry_after(
        self, session: aiohttp.ClientSession, aresponses: ResponsesMockServer
    ) -> None:
        import time as _time

        client = GiveBloodClient(
            session,
            base_url=TEST_BASE_URL,
            token_bundle=TokenBundle(access_token=make_jwt(), expires_at=_time.time() + 3600),
            max_retries=1,
        )
        aresponses.add(
            TEST_HOST,
            EP_ACCOUNT_DETAILS,
            "GET",
            json_response({}, status=429, headers={"Retry-After": "12"}),
        )
        with pytest.raises(GiveBloodRateLimitError) as excinfo:
            await client.async_get_account_details()
        assert excinfo.value.retry_after == 12.0

    async def test_a_write_is_not_retried_after_an_ambiguous_failure(
        self, session: aiohttp.ClientSession, aresponses: ResponsesMockServer
    ) -> None:
        """Replaying a booking POST could create a duplicate NHS appointment."""
        import time as _time

        client = GiveBloodClient(
            session,
            base_url=TEST_BASE_URL,
            token_bundle=TokenBundle(access_token=make_jwt(), expires_at=_time.time() + 3600),
            retry_base_delay=0.0,
        )
        aresponses.add(TEST_HOST, EP_APPOINTMENT_BOOK, "POST", json_response({}, status=503))

        with pytest.raises(GiveBloodBookingError):
            await client.async_book_appointment(
                session_id="CS0000", session_date="2026-10-08", session_time="T1730", venue_id="TSTV1"
            )

    async def test_connection_errors_are_wrapped(self, session: aiohttp.ClientSession) -> None:
        """A dead socket must surface as our own error type, not aiohttp's."""
        import time as _time

        client = GiveBloodClient(
            session,
            base_url=TEST_BASE_URL,
            token_bundle=TokenBundle(access_token=make_jwt(), expires_at=_time.time() + 3600),
            max_retries=1,
        )

        with (
            patch.object(session, "request", side_effect=aiohttp.ClientConnectionError("socket died")),
            pytest.raises(GiveBloodConnectionError),
        ):
            await client.async_get_account_details()

    async def test_a_timeout_is_retried_then_wrapped(self, session: aiohttp.ClientSession) -> None:
        import time as _time

        client = GiveBloodClient(
            session,
            base_url=TEST_BASE_URL,
            token_bundle=TokenBundle(access_token=make_jwt(), expires_at=_time.time() + 3600),
            max_retries=2,
            retry_base_delay=0.0,
            retry_max_delay=0.0,
        )

        with patch.object(session, "request", side_effect=TimeoutError) as mocked:  # noqa: SIM117
            with pytest.raises(GiveBloodConnectionError):
                await client.async_get_account_details()

        assert mocked.call_count == 2, "a safe method should have been retried"


class TestBookingErrors:
    """Booking failures carry the API's field-level validation detail."""

    async def test_validation_errors_are_surfaced(
        self, authed_client: GiveBloodClient, aresponses: ResponsesMockServer
    ) -> None:
        body = {
            "message": "The request was invalid",
            "errors": [{"propertyName": "SessionID", "errorMessage": "Not bookable", "errorCode": "NOT_BOOKABLE"}],
        }
        aresponses.add(TEST_HOST, EP_APPOINTMENT_BOOK, "POST", json_response(body, status=400))

        with pytest.raises(GiveBloodBookingError) as excinfo:
            await authed_client.async_book_appointment(
                session_id="CS0000", session_date="2026-10-08", session_time="T1730", venue_id="TSTV1"
            )

        assert excinfo.value.validation_errors[0]["errorCode"] == "NOT_BOOKABLE"

    async def test_error_messages_do_not_echo_attempted_values(
        self, authed_client: GiveBloodClient, aresponses: ResponsesMockServer
    ) -> None:
        """``attemptedValue`` can contain a password; it must never reach a log line."""
        body = {
            "message": "The request was invalid",
            "errors": [
                {
                    "propertyName": "Password",
                    "errorMessage": "Too short",
                    "errorCode": "TOO_SHORT",
                    "attemptedValue": "hunter2-should-never-be-logged",
                }
            ],
        }
        aresponses.add(TEST_HOST, EP_APPOINTMENT_BOOK, "POST", json_response(body, status=400))

        with pytest.raises(GiveBloodBookingError) as excinfo:
            await authed_client.async_book_appointment(
                session_id="CS0000", session_date="2026-10-08", session_time="T1730", venue_id="TSTV1"
            )

        assert "hunter2-should-never-be-logged" not in str(excinfo.value)


class TestBookingPayloads:
    """Write payloads must match the app's exact key casing."""

    async def test_book_uses_the_apis_sessionID_casing(
        self, authed_client: GiveBloodClient, aresponses: ResponsesMockServer
    ) -> None:
        captured: dict[str, object] = {}

        async def handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
            captured.update(await request.json())
            return json_response({"ok": True})

        aresponses.add(TEST_HOST, EP_APPOINTMENT_BOOK, "POST", handler)
        await authed_client.async_book_appointment(
            session_id="CS0000", session_date=date(2026, 10, 8), session_time="T1730", venue_id="TSTV1"
        )

        assert captured == {
            "sessionID": "CS0000",
            "sessionDate": "2026-10-08T00:00:00",
            "sessionTime": "T1730",
            "venueId": "TSTV1",
            "procedureCode": "WB",
        }

    async def test_cancel_issues_a_delete(
        self, authed_client: GiveBloodClient, aresponses: ResponsesMockServer
    ) -> None:
        aresponses.add(TEST_HOST, "/api/appointments/APPT1", "DELETE", aiohttp.web.Response(status=204))
        await authed_client.async_cancel_appointment("APPT1")


class TestSnapshot:
    """The aggregate read used by long-running consumers."""

    def _register_all(self, aresponses: ResponsesMockServer) -> None:
        aresponses.add(TEST_HOST, EP_ACCOUNT_DETAILS, "GET", json_response(load_fixture("account_details")))
        aresponses.add(TEST_HOST, EP_APPOINTMENTS_FUTURE, "GET", json_response(load_fixture("appointments_future")))
        aresponses.add(TEST_HOST, EP_MESSAGES, "GET", json_response(load_fixture("messages")))
        aresponses.add(TEST_HOST, EP_FEATURES, "GET", json_response(load_fixture("features")), match_querystring=False)
        aresponses.add(
            TEST_HOST,
            EP_FEATURES_FAILOVER,
            "GET",
            json_response(load_fixture("failover")),
            match_querystring=False,
        )
        aresponses.add(TEST_HOST, EP_DONATION_HISTORY, "GET", json_response(load_fixture("donation_history")))

    async def test_clean_snapshot_reports_no_degradation(
        self, authed_client: GiveBloodClient, aresponses: ResponsesMockServer
    ) -> None:
        self._register_all(aresponses)
        snapshot = await authed_client.async_get_snapshot()

        assert not snapshot.partial
        assert snapshot.degraded == ()
        assert snapshot.next_appointment is not None
        assert snapshot.awards_data is not None
        assert snapshot.features is not None

    async def test_a_failed_supplementary_endpoint_degrades_rather_than_fails(
        self, authed_client: GiveBloodClient, aresponses: ResponsesMockServer
    ) -> None:
        """One flaky endpoint must not blank an entire dashboard."""
        aresponses.add(TEST_HOST, EP_ACCOUNT_DETAILS, "GET", json_response(load_fixture("account_details")))
        aresponses.add(TEST_HOST, EP_APPOINTMENTS_FUTURE, "GET", json_response(load_fixture("appointments_future")))
        aresponses.add(TEST_HOST, EP_MESSAGES, "GET", json_response({}, status=500))
        aresponses.add(TEST_HOST, EP_FEATURES, "GET", json_response(load_fixture("features")), match_querystring=False)
        aresponses.add(
            TEST_HOST,
            EP_FEATURES_FAILOVER,
            "GET",
            json_response(load_fixture("failover")),
            match_querystring=False,
        )
        aresponses.add(TEST_HOST, EP_DONATION_HISTORY, "GET", json_response(load_fixture("donation_history")))

        snapshot = await authed_client.async_get_snapshot()

        assert snapshot.partial
        assert "messages" in snapshot.degraded
        assert snapshot.messages is None
        assert snapshot.account.blood_group, "the mandatory read still succeeded"

    async def test_a_failed_account_read_propagates(
        self, authed_client: GiveBloodClient, aresponses: ResponsesMockServer
    ) -> None:
        """The account payload is mandatory — without it there's nothing to show."""
        aresponses.add(TEST_HOST, EP_ACCOUNT_DETAILS, "GET", json_response({}, status=404))
        with pytest.raises(GiveBloodApiError):
            await authed_client.async_get_snapshot()

    async def test_upcoming_appointments_are_deduplicated_across_sources(
        self, authed_client: GiveBloodClient, aresponses: ResponsesMockServer
    ) -> None:
        """The account payload and the appointments endpoint overlap."""
        self._register_all(aresponses)
        snapshot = await authed_client.async_get_snapshot()

        keys = [(a.session_id, a.time) for a in snapshot.upcoming_appointments]
        assert len(keys) == len(set(keys))

    async def test_donations_can_be_skipped(
        self, authed_client: GiveBloodClient, aresponses: ResponsesMockServer
    ) -> None:
        aresponses.add(TEST_HOST, EP_ACCOUNT_DETAILS, "GET", json_response(load_fixture("account_details")))
        aresponses.add(TEST_HOST, EP_APPOINTMENTS_FUTURE, "GET", json_response(load_fixture("appointments_future")))
        aresponses.add(TEST_HOST, EP_MESSAGES, "GET", json_response(load_fixture("messages")))
        aresponses.add(TEST_HOST, EP_FEATURES, "GET", json_response(load_fixture("features")), match_querystring=False)
        aresponses.add(
            TEST_HOST,
            EP_FEATURES_FAILOVER,
            "GET",
            json_response(load_fixture("failover")),
            match_querystring=False,
        )

        snapshot = await authed_client.async_get_snapshot(include_donations=False)

        assert snapshot.donations is None
        assert not snapshot.partial


class TestLoginShortcut:
    """Login already embeds the account payload — no second call needed."""

    async def test_login_returns_account_details(
        self, credentialed_client: GiveBloodClient, aresponses: ResponsesMockServer
    ) -> None:
        from nhs_give_blood.const import EP_LOGIN

        from .fixtures import login_payload

        aresponses.add(TEST_HOST, EP_LOGIN, "POST", json_response(login_payload()))
        account = await credentialed_client.async_login()

        assert account is not None
        assert account.blood_group
        assert credentialed_client.donor_id == "D0000000"
