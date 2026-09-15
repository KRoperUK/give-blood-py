"""Exception hierarchy for :mod:`nhs_give_blood`.

Consumers should branch on the two booleans carried by
:class:`GiveBloodAuthError` rather than on the concrete class:

* ``reauth_required`` — the stored credentials/tokens are dead. A human must
  supply new ones (in Home Assistant terms: start a reauth flow).
* ``transient`` — the failure was infrastructural. Retry later; do not prompt.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "GiveBloodApiError",
    "GiveBloodAuthError",
    "GiveBloodBookingError",
    "GiveBloodConnectionError",
    "GiveBloodError",
    "GiveBloodInvalidCredentialsError",
    "GiveBloodRateLimitError",
    "GiveBloodTokenExpiredError",
    "classify_auth_error",
    "error_summary",
]

#: Cap on how much of a response body ends up in an exception message. Keeps
#: tokens and donor detail out of logs even when the server echoes them back.
_MAX_DETAIL_CHARS = 200


def _truncate(value: object, limit: int = _MAX_DETAIL_CHARS) -> str:
    """Render ``value`` as a short string safe to put in a log line."""
    text = value if isinstance(value, str) else repr(value)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


class GiveBloodError(Exception):
    """Base class for every error raised by this library."""


class GiveBloodConnectionError(GiveBloodError):
    """The API host could not be reached, or the connection failed mid-request.

    Always transient by nature — there is no response to classify.
    """


class GiveBloodApiError(GiveBloodError):
    """The API returned an unexpected non-2xx response."""

    def __init__(self, status: int | None, message: str, *, details: Any = None) -> None:
        self.status = status
        self.details = details
        super().__init__(f"HTTP {status}: {message}" if status else message)


class GiveBloodRateLimitError(GiveBloodApiError):
    """The API rate-limited the client (HTTP 429).

    ``retry_after`` is the server's ``Retry-After`` in seconds when supplied.
    """

    def __init__(self, status: int | None, message: str, *, retry_after: float | None = None, details: Any = None):
        self.retry_after = retry_after
        super().__init__(status, message, details=details)


class GiveBloodAuthError(GiveBloodError):
    """Authentication or authorisation failed."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status: int | None = None,
        reauth_required: bool = True,
        transient: bool = False,
        details: Any = None,
    ) -> None:
        self.code = code
        self.status = status
        self.reauth_required = reauth_required
        self.transient = transient
        self.details = details
        super().__init__(message)


class GiveBloodInvalidCredentialsError(GiveBloodAuthError):
    """The supplied username/password was rejected."""

    def __init__(self, message: str = "Username or password rejected", **kwargs: Any) -> None:
        kwargs.setdefault("reauth_required", True)
        kwargs.setdefault("transient", False)
        super().__init__(message, **kwargs)


class GiveBloodTokenExpiredError(GiveBloodAuthError):
    """The access token has aged out and a refresh is required.

    Raised only when a refresh is impossible (no refresh token, or the refresh
    itself was rejected). A recoverable expiry is handled internally and never
    surfaces.
    """

    def __init__(self, message: str = "Access token expired and could not be refreshed", **kwargs: Any) -> None:
        kwargs.setdefault("code", "TOKEN_EXPIRED")
        super().__init__(message, **kwargs)


class GiveBloodBookingError(GiveBloodApiError):
    """A booking, reschedule or cancellation was refused by the API.

    ``validation_errors`` holds the API's per-field complaints when it returned
    its standard FluentValidation envelope.
    """

    def __init__(
        self,
        status: int | None,
        message: str,
        *,
        validation_errors: list[dict[str, Any]] | None = None,
        details: Any = None,
    ) -> None:
        self.validation_errors = validation_errors or []
        super().__init__(status, message, details=details)


def error_summary(body: Any) -> str:
    """Summarise an API error body for a log line, without leaking donor data.

    The API uses two shapes: a bare ``{"message": ...}`` and a
    FluentValidation envelope with an ``errors`` array. ``attemptedValue`` in
    that envelope echoes back whatever the caller sent — which can be a
    password — so only ``errorCode``/``errorMessage`` are ever surfaced.
    """
    if not isinstance(body, dict):
        return _truncate(body)
    parts: list[str] = []
    if message := body.get("message"):
        parts.append(_truncate(message))
    errors = body.get("errors")
    if isinstance(errors, list):
        for item in errors[:5]:
            if not isinstance(item, dict):
                continue
            code = item.get("errorCode") or item.get("propertyName")
            detail = item.get("errorMessage")
            parts.append(f"{code}: {_truncate(detail, 80)}" if detail else str(code))
    return "; ".join(p for p in parts if p) or _truncate(body)


def classify_auth_error(status: int | None, body: Any) -> GiveBloodAuthError:
    """Map an auth-endpoint failure onto the right exception subclass.

    The distinction that matters to callers is *transient* (retry later) versus
    *reauth_required* (stop polling and ask a human).
    """
    code = body.get("code") if isinstance(body, dict) else None
    summary = error_summary(body)

    if status is not None and (status >= 500 or status == 429):
        return GiveBloodAuthError(
            f"Authentication service unavailable: {summary}",
            code=code,
            status=status,
            reauth_required=False,
            transient=True,
            details=body,
        )

    if code == "TOKEN_EXPIRED":
        return GiveBloodTokenExpiredError(status=status, details=body)

    if status in (400, 401, 403):
        return GiveBloodInvalidCredentialsError(
            f"Authentication rejected: {summary}",
            code=code,
            status=status,
            details=body,
        )

    return GiveBloodAuthError(
        f"Authentication failed: {summary}",
        code=code,
        status=status,
        reauth_required=True,
        transient=False,
        details=body,
    )
