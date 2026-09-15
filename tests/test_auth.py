"""Token lifecycle tests: expiry maths, the auth ladder, and rotation callbacks."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest
from aresponses import ResponsesMockServer

from nhs_give_blood import (
    AuthManager,
    GiveBloodConnectionError,
    GiveBloodInvalidCredentialsError,
    GiveBloodTokenExpiredError,
    TokenBundle,
    decode_jwt_claims,
)
from nhs_give_blood.const import EP_LOGIN, EP_LOGOUT, EP_REFRESH

from .fixtures import (
    SYNTHETIC_PASSWORD,
    SYNTHETIC_USERNAME,
    TEST_BASE_URL,
    TEST_HOST,
    json_response,
    login_payload,
    make_jwt,
)


def _manager(session: aiohttp.ClientSession, **kwargs: Any) -> AuthManager:
    """Build an AuthManager pointed at the mock host."""
    return AuthManager(session, base_url=TEST_BASE_URL, **kwargs)


class TestTokenBundle:
    """The storage contract with consumers."""

    def test_round_trips_through_a_mapping(self) -> None:
        bundle = TokenBundle(access_token="a", refresh_token="r", expires_at=123.0)
        assert TokenBundle.from_mapping(bundle.as_dict()) == bundle

    def test_from_mapping_tolerates_none_and_junk(self) -> None:
        assert TokenBundle.from_mapping(None) == TokenBundle()
        assert TokenBundle.from_mapping({"expires_at": "not a number"}).expires_at == 0.0

    def test_fresh_requires_both_token_and_future_expiry(self) -> None:
        assert not TokenBundle().is_fresh
        assert not TokenBundle(access_token="a").is_fresh, "no expiry means treat as stale"
        assert not TokenBundle(access_token="a", expires_at=time.time() - 1).is_fresh
        assert TokenBundle(access_token="a", expires_at=time.time() + 3600).is_fresh

    def test_expiry_margin_makes_a_nearly_dead_token_stale(self) -> None:
        """A token with 10s left must not be handed out — the request would race its expiry."""
        assert not TokenBundle(access_token="a", expires_at=time.time() + 10).is_fresh

    def test_repr_does_not_leak_token_material(self) -> None:
        text = repr(TokenBundle(access_token="supersecret", refresh_token="alsosecret"))  # pii-allow
        assert "supersecret" not in text  # pii-allow
        assert "alsosecret" not in text  # pii-allow
        assert "set" in text


class TestJwtDecoding:
    """Expiry comes from the token itself; the API never sends expires_in."""

    def test_reads_claims_from_a_wellformed_token(self) -> None:
        claims = decode_jwt_claims(make_jwt(donor_id="D1234567", can_book=False))  # pii-allow - synthetic
        assert claims["donor_id"] == "D1234567"  # pii-allow - synthetic
        assert claims["can_book"] is False
        assert "exp" in claims

    @pytest.mark.parametrize("token", ["", "notajwt", "a.b.c", "..", "x"])
    def test_malformed_tokens_yield_empty_claims(self, token: str) -> None:
        assert decode_jwt_claims(token) == {}


class TestLogin:
    """Password authentication."""

    async def test_stores_tokens_and_derives_expiry(
        self, session: aiohttp.ClientSession, aresponses: ResponsesMockServer
    ) -> None:
        aresponses.add(TEST_HOST, EP_LOGIN, "POST", json_response(login_payload()))
        manager = _manager(session, username=SYNTHETIC_USERNAME, password=SYNTHETIC_PASSWORD)

        response = await manager.login()

        assert response.account_details is not None
        assert manager.is_authenticated
        assert manager.tokens.expires_at > time.time()
        assert manager.donor_id == "D0000000"
        assert manager.can_book is True

    async def test_rejected_credentials_raise_invalid_credentials(
        self, session: aiohttp.ClientSession, aresponses: ResponsesMockServer
    ) -> None:
        aresponses.add(TEST_HOST, EP_LOGIN, "POST", json_response({"message": "Nope"}, status=401))
        manager = _manager(session, username=SYNTHETIC_USERNAME, password=SYNTHETIC_PASSWORD)

        with pytest.raises(GiveBloodInvalidCredentialsError) as excinfo:
            await manager.login()

        assert excinfo.value.reauth_required
        assert not excinfo.value.transient

    async def test_server_error_is_transient_not_reauth(
        self, session: aiohttp.ClientSession, aresponses: ResponsesMockServer
    ) -> None:
        """A 503 must not send the consumer into a reauth prompt loop."""
        aresponses.add(TEST_HOST, EP_LOGIN, "POST", json_response({}, status=503))
        manager = _manager(session, username=SYNTHETIC_USERNAME, password=SYNTHETIC_PASSWORD)

        with pytest.raises(Exception) as excinfo:  # noqa: B017 - asserting on attributes below
            await manager.login()

        assert getattr(excinfo.value, "transient", False) is True
        assert getattr(excinfo.value, "reauth_required", True) is False

    async def test_login_without_credentials_fails_fast(self, session: aiohttp.ClientSession) -> None:
        with pytest.raises(Exception, match="No username/password"):
            await _manager(session).login()

    async def test_connection_failure_is_wrapped(self, session: aiohttp.ClientSession) -> None:
        """A dead socket must surface as our own error type, not aiohttp's."""
        manager = _manager(session, username=SYNTHETIC_USERNAME, password=SYNTHETIC_PASSWORD)

        with (
            patch.object(session, "post", side_effect=aiohttp.ClientConnectionError("socket died")),
            pytest.raises(GiveBloodConnectionError),
        ):
            await manager.login()

    async def test_timeout_is_wrapped(self, session: aiohttp.ClientSession) -> None:
        manager = _manager(session, username=SYNTHETIC_USERNAME, password=SYNTHETIC_PASSWORD)

        with patch.object(session, "post", side_effect=TimeoutError), pytest.raises(GiveBloodConnectionError):
            await manager.login()


class TestRefresh:
    """Refresh-token exchange."""

    async def test_rotates_both_tokens(self, session: aiohttp.ClientSession, aresponses: ResponsesMockServer) -> None:
        new_access = make_jwt(ttl=3600)
        aresponses.add(
            TEST_HOST,
            EP_REFRESH,
            "POST",
            json_response({"accessToken": new_access, "refreshToken": "rotated"}),
        )
        manager = _manager(session, token_bundle=TokenBundle(access_token="old", refresh_token="synthetic-original"))

        bundle = await manager.refresh()

        assert bundle.access_token == new_access
        assert bundle.refresh_token == "rotated"

    async def test_keeps_existing_refresh_token_when_none_returned(
        self, session: aiohttp.ClientSession, aresponses: ResponsesMockServer
    ) -> None:
        aresponses.add(TEST_HOST, EP_REFRESH, "POST", json_response({"accessToken": make_jwt()}))
        manager = _manager(session, token_bundle=TokenBundle(access_token="old", refresh_token="synthetic-original"))

        assert (await manager.refresh()).refresh_token == "synthetic-original"

    async def test_without_a_refresh_token_it_raises(self, session: aiohttp.ClientSession) -> None:
        with pytest.raises(GiveBloodTokenExpiredError):
            await _manager(session).refresh()

    async def test_response_without_access_token_raises(
        self, session: aiohttp.ClientSession, aresponses: ResponsesMockServer
    ) -> None:
        aresponses.add(TEST_HOST, EP_REFRESH, "POST", json_response({}))
        manager = _manager(session, token_bundle=TokenBundle(refresh_token="r"))

        with pytest.raises(GiveBloodTokenExpiredError):
            await manager.refresh()


class TestAuthLadder:
    """Fresh token → refresh → password login, in that order."""

    async def test_fresh_token_is_reused_without_any_request(self, session: aiohttp.ClientSession) -> None:
        """No aresponses registration: any HTTP call here would fail the test."""
        token = make_jwt()
        manager = _manager(session, token_bundle=TokenBundle(access_token=token, expires_at=time.time() + 3600))

        assert await manager.async_get_access_token() == token

    async def test_stale_token_triggers_refresh(
        self, session: aiohttp.ClientSession, aresponses: ResponsesMockServer
    ) -> None:
        refreshed = make_jwt()
        aresponses.add(TEST_HOST, EP_REFRESH, "POST", json_response({"accessToken": refreshed}))
        manager = _manager(session, token_bundle=TokenBundle(access_token="stale", refresh_token="r"))

        assert await manager.async_get_access_token() == refreshed

    async def test_dead_refresh_token_falls_through_to_login(
        self, session: aiohttp.ClientSession, aresponses: ResponsesMockServer
    ) -> None:
        """NHSBT rotates refresh tokens, so a stale stored one is routine, not fatal."""
        aresponses.add(TEST_HOST, EP_REFRESH, "POST", json_response({"message": "dead"}, status=401))
        aresponses.add(TEST_HOST, EP_LOGIN, "POST", json_response(login_payload()))
        manager = _manager(
            session,
            token_bundle=TokenBundle(access_token="stale", refresh_token="dead"),
            username=SYNTHETIC_USERNAME,
            password=SYNTHETIC_PASSWORD,
        )

        assert await manager.async_get_access_token()
        assert manager.is_authenticated

    async def test_transient_refresh_failure_does_not_fall_through_to_login(
        self, session: aiohttp.ClientSession, aresponses: ResponsesMockServer
    ) -> None:
        """A 500 during refresh must surface, not burn a password attempt."""
        aresponses.add(TEST_HOST, EP_REFRESH, "POST", json_response({}, status=500))
        manager = _manager(
            session,
            token_bundle=TokenBundle(access_token="stale", refresh_token="r"),
            username=SYNTHETIC_USERNAME,
            password=SYNTHETIC_PASSWORD,
        )

        with pytest.raises(Exception) as excinfo:  # noqa: B017 - attribute assertion below
            await manager.async_get_access_token()
        assert getattr(excinfo.value, "transient", False) is True

    async def test_no_tokens_and_no_credentials_raises(self, session: aiohttp.ClientSession) -> None:
        with pytest.raises(GiveBloodTokenExpiredError):
            await _manager(session).async_get_access_token()


class TestTokenListener:
    """Consumers persist tokens from this callback."""

    async def test_fires_on_rotation_with_the_new_bundle(
        self, session: aiohttp.ClientSession, aresponses: ResponsesMockServer
    ) -> None:
        seen: list[TokenBundle] = []
        aresponses.add(TEST_HOST, EP_LOGIN, "POST", json_response(login_payload()))
        manager = _manager(
            session,
            username=SYNTHETIC_USERNAME,
            password=SYNTHETIC_PASSWORD,
            on_token_update=seen.append,
        )

        await manager.login()

        assert len(seen) == 1
        assert seen[0].access_token

    async def test_accepts_an_async_listener(
        self, session: aiohttp.ClientSession, aresponses: ResponsesMockServer
    ) -> None:
        seen: list[TokenBundle] = []

        async def listener(bundle: TokenBundle) -> None:
            seen.append(bundle)

        aresponses.add(TEST_HOST, EP_LOGIN, "POST", json_response(login_payload()))
        manager = _manager(session, username=SYNTHETIC_USERNAME, password=SYNTHETIC_PASSWORD, on_token_update=listener)

        await manager.login()
        assert len(seen) == 1

    async def test_a_raising_listener_does_not_break_authentication(
        self, session: aiohttp.ClientSession, aresponses: ResponsesMockServer
    ) -> None:
        def listener(_: TokenBundle) -> None:
            raise RuntimeError("consumer bug")

        aresponses.add(TEST_HOST, EP_LOGIN, "POST", json_response(login_payload()))
        manager = _manager(session, username=SYNTHETIC_USERNAME, password=SYNTHETIC_PASSWORD, on_token_update=listener)

        await manager.login()
        assert manager.is_authenticated

    async def test_apply_tokens_does_not_fire_the_listener(self, session: aiohttp.ClientSession) -> None:
        """Loading from storage is not a rotation and must not trigger a write-back."""
        seen: list[TokenBundle] = []
        manager = _manager(session, on_token_update=seen.append)

        manager.apply_tokens(TokenBundle(access_token="a", refresh_token="r"))

        assert seen == []


class TestLogout:
    """Signing out must clear local state even if the server call fails."""

    async def test_clears_tokens_and_calls_the_api(
        self, session: aiohttp.ClientSession, aresponses: ResponsesMockServer
    ) -> None:
        aresponses.add(TEST_HOST, EP_LOGOUT, "POST", json_response({}))
        manager = _manager(session, token_bundle=TokenBundle(access_token="a", refresh_token="r"))

        await manager.logout()

        assert manager.tokens == TokenBundle()

    async def test_still_clears_tokens_when_the_call_fails(
        self, session: aiohttp.ClientSession, aresponses: ResponsesMockServer
    ) -> None:
        aresponses.add(TEST_HOST, EP_LOGOUT, "POST", json_response({}, status=500))
        manager = _manager(session, token_bundle=TokenBundle(access_token="a", refresh_token="r"))

        await manager.logout()

        assert manager.tokens == TokenBundle()

    async def test_no_refresh_token_skips_the_call(self, session: aiohttp.ClientSession) -> None:
        """No aresponses registration — a request would fail the test."""
        manager = _manager(session, token_bundle=TokenBundle(access_token="a"))
        await manager.logout()
        assert manager.tokens == TokenBundle()
