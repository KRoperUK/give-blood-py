"""Shared test helpers.

Every fixture under ``tests/fixtures/`` was produced from a live capture by
``scripts/sanitise_capture.py`` and contains only synthetic identities. See
``test_fixture_hygiene.py`` for the check that keeps it that way.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).parent / "fixtures"

#: Host aresponses matches on. The client is always constructed with
#: ``base_url`` pointing here so no test can touch the real API.
TEST_HOST = "test.invalid"
TEST_BASE_URL = f"https://{TEST_HOST}"

SYNTHETIC_USERNAME = "donor@example.invalid"
SYNTHETIC_PASSWORD = "not-a-real-password"  # noqa: S105 - synthetic test value


def load_fixture(name: str) -> Any:
    """Load a JSON fixture by stem, e.g. ``load_fixture("account_details")``."""
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


def _b64(payload: dict[str, Any]) -> str:
    """URL-safe base64 without padding, the way JWTs encode segments."""
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def make_jwt(
    *,
    ttl: float = 1800.0,
    donor_id: str = "D0000000",
    user_id: str = "U0000000",
    can_book: bool = True,
) -> str:
    """Build an unsigned JWT shaped like a real access token.

    The signature is meaningless: the library reads ``exp`` without verifying,
    so a valid signature would add nothing. Pass a negative ``ttl`` for an
    already-expired token.
    """
    header = _b64({"typ": "JWT", "alg": "RS256"})
    claims = _b64(
        {
            "exp": int(time.time() + ttl),
            "donor_id": donor_id,
            "user_id": user_id,
            "can_book": can_book,
        }
    )
    return f"{header}.{claims}.synthetic-signature"


def login_payload(*, ttl: float = 1800.0) -> dict[str, Any]:
    """The login fixture with a usable, non-expired access token."""
    payload = load_fixture("login_response")
    payload["accessToken"] = make_jwt(ttl=ttl)
    payload["refreshToken"] = "synthetic-refresh-token"
    return payload


def json_response(payload: Any, status: int = 200, headers: dict[str, str] | None = None) -> Any:
    """Build a JSON response for aresponses.

    aresponses doesn't ship a JSON helper, and passing a dict straight to
    ``aresponses.add`` yields a text/plain body — which this client tolerates but
    which would stop the tests from exercising the real content type.
    """
    from aiohttp import web

    return web.json_response(payload, status=status, headers=headers)
