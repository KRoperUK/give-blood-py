"""Token handling for the NHS Give Blood app API.

The API issues a short-lived RS256 JWT access token (~30 minutes) plus an
opaque refresh token. It does **not** return an ``expires_in``, so expiry is
read from the JWT's own ``exp`` claim. The signature is not verified: this is a
client reading its own token to decide when to refresh, not a resource server
making a trust decision, and the client has no access to NHSBT's public keys.
"""

from __future__ import annotations

import base64
import binascii
import inspect
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import aiohttp

from .const import (
    APP_API_KEY,
    BASE_URL,
    CLIENT_TYPE_APP,
    DEFAULT_CLIENT_VERSION,
    DEFAULT_TIMEOUT,
    EP_LOGIN,
    EP_LOGOUT,
    EP_REFRESH,
    TOKEN_EXPIRY_MARGIN,
)
from .exceptions import (
    GiveBloodAuthError,
    GiveBloodConnectionError,
    GiveBloodTokenExpiredError,
    classify_auth_error,
)
from .models import LoginResponse

_LOGGER = logging.getLogger(__name__)

__all__ = ["AuthManager", "TokenBundle", "TokenListener", "build_headers", "decode_jwt_claims"]

#: Called with the new bundle whenever tokens rotate. May be sync or async —
#: consumers persist tokens from here (Home Assistant writes them to the config
#: entry) so a restart doesn't force a fresh login.
TokenListener = Callable[["TokenBundle"], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class TokenBundle:
    """An access/refresh token pair with the access token's expiry.

    This is the serialisation contract with consumers: :meth:`as_dict` and
    :meth:`from_mapping` are what Home Assistant round-trips through its config
    entry, so the field names are part of the public API.
    """

    access_token: str = ""
    refresh_token: str = ""
    #: Unix timestamp the access token expires, 0 when unknown.
    expires_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        """Serialise for storage."""
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> TokenBundle:
        """Rebuild from stored data, tolerating missing or malformed fields."""
        if not data:
            return cls()
        try:
            expires_at = float(data.get("expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0.0
        return cls(
            access_token=str(data.get("access_token") or ""),
            refresh_token=str(data.get("refresh_token") or ""),
            expires_at=expires_at,
        )

    @property
    def is_fresh(self) -> bool:
        """True when the access token exists and isn't within the expiry margin."""
        if not self.access_token:
            return False
        if not self.expires_at:
            # No expiry known: treat as stale so the caller refreshes rather
            # than firing a request that is very likely to 401.
            return False
        return time.time() < self.expires_at - TOKEN_EXPIRY_MARGIN

    def __repr__(self) -> str:
        """Redacted repr — bundles end up in debug logs and diagnostics."""
        return (
            f"TokenBundle(access_token={'set' if self.access_token else 'unset'}, "
            f"refresh_token={'set' if self.refresh_token else 'unset'}, "
            f"expires_at={self.expires_at})"
        )


def decode_jwt_claims(token: str) -> dict[str, Any]:
    """Return a JWT's claims without verifying its signature.

    Returns an empty dict for anything that isn't a decodable JWT, so callers
    can treat "no claims" and "bad token" identically.
    """
    if not token or token.count(".") < 1:
        return {}
    payload = token.split(".")[1]
    padded = payload + "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded)
        claims = json.loads(raw)
    except (binascii.Error, ValueError, TypeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def _expiry_from_token(token: str) -> float:
    """Extract the ``exp`` claim as a unix timestamp, or 0 when absent."""
    exp: object = decode_jwt_claims(token).get("exp")
    if not isinstance(exp, (int, float, str)):
        return 0.0
    try:
        return float(exp)
    except (TypeError, ValueError):
        return 0.0


def build_headers(
    *,
    client_version: str = DEFAULT_CLIENT_VERSION,
    client_type: str = CLIENT_TYPE_APP,
    access_token: str | None = None,
) -> dict[str, str]:
    """Build the header set the app sends on every API call.

    The cache-busting trio and the ``Nhsbt-*`` pair are not optional decoration:
    the API varies feature-flag and failover responses by client type/version,
    and returns stale bodies through intermediary caches without them.
    """
    headers = {
        "ApiKey": APP_API_KEY,
        "Nhsbt-Client-Type": client_type,
        "Nhsbt-Client-Version": client_version,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Cache-Control": "no-cache, no-store",
        "Pragma": "no-cache",
        "Expires": "0",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return headers


class AuthManager:
    """Owns the token lifecycle: login, refresh-on-demand, logout.

    The aiohttp session is caller-owned and never closed here — Home Assistant
    passes its shared session in.
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
        on_token_update: TokenListener | None = None,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._tokens = token_bundle or TokenBundle()
        self._base_url = base_url.rstrip("/")
        self._client_version = client_version
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._on_token_update = on_token_update

    # -- state ------------------------------------------------------------

    @property
    def tokens(self) -> TokenBundle:
        """Current token bundle."""
        return self._tokens

    @property
    def is_authenticated(self) -> bool:
        """True when a usable, unexpired access token is held."""
        return self._tokens.is_fresh

    @property
    def can_refresh(self) -> bool:
        """True when a refresh token is held."""
        return bool(self._tokens.refresh_token)

    @property
    def can_login(self) -> bool:
        """True when username and password are both held."""
        return bool(self._username and self._password)

    @property
    def donor_id(self) -> str | None:
        """``donor_id`` claim from the access token.

        Cheap, stable account identifier — no extra API call needed, which is
        what makes it the right choice for a config-entry unique id.
        """
        value = decode_jwt_claims(self._tokens.access_token).get("donor_id")
        return str(value) if value else None

    @property
    def can_book(self) -> bool | None:
        """``can_book`` claim: whether the account is allowed to book at all."""
        value = decode_jwt_claims(self._tokens.access_token).get("can_book")
        return bool(value) if isinstance(value, bool) else None

    def export_tokens(self) -> TokenBundle:
        """Snapshot the tokens for persistence."""
        return self._tokens

    def apply_tokens(self, bundle: TokenBundle) -> None:
        """Replace the held tokens without notifying listeners.

        Use this when loading from storage; rotations that happen *during*
        operation go through the internal setter so listeners fire.
        """
        self._tokens = bundle

    def set_credentials(self, username: str | None, password: str | None) -> None:
        """Update the username/password used by the login fallback."""
        self._username = username
        self._password = password

    # -- token plumbing ---------------------------------------------------

    async def _store(self, access_token: str, refresh_token: str) -> None:
        """Record a rotated token pair and notify the listener."""
        bundle = TokenBundle(
            access_token=access_token,
            refresh_token=refresh_token or self._tokens.refresh_token,
            expires_at=_expiry_from_token(access_token),
        )
        if bundle == self._tokens:
            return
        self._tokens = bundle
        if self._on_token_update is None:
            return
        try:
            result = self._on_token_update(bundle)
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 - a bad listener must not break auth
            _LOGGER.exception("Token update listener raised; tokens were still rotated")

    async def _post(self, path: str, payload: dict[str, Any]) -> Any:
        """POST to an auth endpoint and return the decoded body.

        Auth endpoints bypass the client's retry loop on purpose: a retried
        login is a second password attempt against an account-lockout policy.
        """
        url = f"{self._base_url}{path}"
        headers = build_headers(client_version=self._client_version)
        try:
            async with self._session.post(url, json=payload, headers=headers, timeout=self._timeout) as response:
                body = await _decode(response)
                if response.status >= 400:
                    raise classify_auth_error(response.status, body)
                return body
        except aiohttp.ClientError as err:
            raise GiveBloodConnectionError(f"Could not reach {path}: {err}") from err
        except TimeoutError as err:
            raise GiveBloodConnectionError(f"Timed out calling {path}") from err

    # -- flows ------------------------------------------------------------

    async def login(self) -> LoginResponse:
        """Authenticate with username and password.

        ``plasmaLoginAllowed`` mirrors the app: it opts the response into plasma
        eligibility fields. ``biometricSignature`` is null for a password login.
        """
        if not self.can_login:
            raise GiveBloodAuthError(
                "No username/password available to log in with",
                reauth_required=True,
            )
        body = await self._post(
            EP_LOGIN,
            {
                "username": self._username,
                "password": self._password,
                "plasmaLoginAllowed": True,
                "biometricSignature": None,
            },
        )
        parsed = LoginResponse.model_validate(body)
        await self._store(parsed.access_token, parsed.refresh_token)
        _LOGGER.debug("Logged in successfully; access token valid until %s", self._tokens.expires_at)
        return parsed

    async def refresh(self) -> TokenBundle:
        """Exchange the refresh token for a new pair."""
        if not self.can_refresh:
            raise GiveBloodTokenExpiredError("No refresh token available", reauth_required=True)
        body = await self._post(EP_REFRESH, {"refreshToken": self._tokens.refresh_token})
        access = body.get("accessToken") if isinstance(body, dict) else None
        if not access:
            raise GiveBloodTokenExpiredError("Refresh response contained no access token", reauth_required=True)
        await self._store(access, body.get("refreshToken", ""))
        return self._tokens

    async def async_get_access_token(self) -> str:
        """Return a usable access token, obtaining one if needed.

        The ladder is: reuse a fresh token → refresh → password login. A failed
        refresh falls through to login rather than surfacing, because NHSBT
        rotates refresh tokens and a stale stored one is routine.
        """
        if self._tokens.is_fresh:
            return self._tokens.access_token

        if self.can_refresh:
            try:
                return (await self.refresh()).access_token
            except GiveBloodAuthError as err:
                if err.transient or not self.can_login:
                    raise
                _LOGGER.debug("Refresh rejected (%s); falling back to password login", err)

        if self.can_login:
            return (await self.login()).access_token

        raise GiveBloodTokenExpiredError(
            "No fresh token, no usable refresh token, and no credentials to log in with",
            reauth_required=True,
        )

    async def logout(self) -> None:
        """Invalidate the refresh token server-side and clear local state.

        Best effort: a failed logout still clears the local tokens, because the
        caller's intent was to stop being signed in.
        """
        refresh_token = self._tokens.refresh_token
        self._tokens = TokenBundle()
        if not refresh_token:
            return
        try:
            await self._post(EP_LOGOUT, {"refreshToken": refresh_token})
        except (GiveBloodAuthError, GiveBloodConnectionError) as err:
            _LOGGER.debug("Logout call failed, local tokens cleared anyway: %s", err)


async def _decode(response: aiohttp.ClientResponse) -> Any:
    """Decode a response body as JSON, tolerating wrong content types.

    The API sends its Next.js 404 page as HTML for web-only routes, so a decode
    failure is turned into a string rather than an exception.
    """
    try:
        return await response.json(content_type=None)
    except (aiohttp.ContentTypeError, ValueError, json.JSONDecodeError):
        return await response.text()
