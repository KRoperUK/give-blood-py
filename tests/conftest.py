"""Pytest fixtures for the NHS Give Blood client tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import aiohttp
import pytest

from nhs_give_blood import GiveBloodClient, TokenBundle

from .fixtures import SYNTHETIC_PASSWORD, SYNTHETIC_USERNAME, TEST_BASE_URL, make_jwt


@pytest.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    """A caller-owned aiohttp session, closed after each test."""
    async with aiohttp.ClientSession() as client_session:
        yield client_session


@pytest.fixture
def authed_client(session: aiohttp.ClientSession) -> GiveBloodClient:
    """A client holding a fresh token, so requests skip the auth ladder."""
    return GiveBloodClient(
        session,
        base_url=TEST_BASE_URL,
        token_bundle=TokenBundle(
            access_token=make_jwt(),
            refresh_token="synthetic-refresh-token",
            expires_at=__import__("time").time() + 1800,
        ),
    )


@pytest.fixture
def credentialed_client(session: aiohttp.ClientSession) -> GiveBloodClient:
    """A client with credentials but no tokens, forcing a login on first use."""
    return GiveBloodClient(
        session,
        base_url=TEST_BASE_URL,
        username=SYNTHETIC_USERNAME,
        password=SYNTHETIC_PASSWORD,
    )
