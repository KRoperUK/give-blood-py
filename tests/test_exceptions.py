"""Exception classification tests.

The classification booleans drive consumer behaviour — a transient failure
misclassified as a credential failure produces spurious reauth prompts, and the
reverse produces a silent stall — so they get direct tests.
"""

from __future__ import annotations

import pytest

from nhs_give_blood import (
    GiveBloodApiError,
    GiveBloodAuthError,
    GiveBloodBookingError,
    GiveBloodInvalidCredentialsError,
    GiveBloodRateLimitError,
    GiveBloodTokenExpiredError,
)
from nhs_give_blood.exceptions import classify_auth_error, error_summary


class TestClassifyAuthError:
    """Mapping an auth failure onto retry-later vs ask-a-human."""

    @pytest.mark.parametrize("status", [500, 502, 503, 504, 429])
    def test_server_side_failures_are_transient(self, status: int) -> None:
        error = classify_auth_error(status, {"message": "later"})
        assert error.transient
        assert not error.reauth_required

    @pytest.mark.parametrize("status", [400, 401, 403])
    def test_client_side_failures_need_reauth(self, status: int) -> None:
        error = classify_auth_error(status, {"message": "no"})
        assert isinstance(error, GiveBloodInvalidCredentialsError)
        assert error.reauth_required
        assert not error.transient

    def test_token_expired_code_wins_over_status(self) -> None:
        """The API returns TOKEN_EXPIRED with a 401; the code is the specific signal."""
        error = classify_auth_error(401, {"code": "TOKEN_EXPIRED"})
        assert isinstance(error, GiveBloodTokenExpiredError)

    def test_token_expired_from_a_5xx_stays_transient(self) -> None:
        """A 500 is infrastructure, whatever code it happens to carry."""
        error = classify_auth_error(500, {"code": "TOKEN_EXPIRED"})
        assert error.transient

    def test_unclassifiable_status_defaults_to_reauth(self) -> None:
        error = classify_auth_error(418, {"message": "teapot"})
        assert type(error) is GiveBloodAuthError
        assert error.reauth_required

    def test_status_none_is_handled(self) -> None:
        assert classify_auth_error(None, None).reauth_required

    def test_non_dict_body_is_handled(self) -> None:
        assert classify_auth_error(401, "<html>gateway error</html>").reauth_required


class TestErrorSummary:
    """Summaries go into log lines, so they must not carry secrets."""

    def test_summarises_a_simple_message(self) -> None:
        assert error_summary({"message": "The request was invalid"}) == "The request was invalid"

    def test_summarises_the_validation_envelope(self) -> None:
        summary = error_summary(
            {
                "message": "The request was invalid",
                "errors": [{"errorCode": "PLATFORM_EMPTY", "errorMessage": "'Platform' must not be empty."}],
            }
        )
        assert "PLATFORM_EMPTY" in summary
        assert "must not be empty" in summary

    def test_never_includes_attempted_values(self) -> None:
        """``attemptedValue`` echoes the caller's input, which can be a password."""
        summary = error_summary(
            {
                "errors": [
                    {
                        "errorCode": "TOO_SHORT",
                        "errorMessage": "Too short",
                        "attemptedValue": "hunter2-must-not-be-logged",
                    }
                ]
            }
        )
        assert "hunter2-must-not-be-logged" not in summary

    def test_caps_runaway_bodies(self) -> None:
        summary = error_summary({"message": "x" * 5000})
        assert len(summary) < 300
        assert summary.endswith("…")

    def test_collapses_whitespace(self) -> None:
        assert error_summary({"message": "line one\n   line two"}) == "line one line two"

    def test_caps_the_number_of_errors_reported(self) -> None:
        body = {"errors": [{"errorCode": f"E{index}"} for index in range(50)]}
        assert error_summary(body).count(";") < 10

    def test_handles_non_dict_and_malformed_bodies(self) -> None:
        assert error_summary("plain text")
        assert error_summary({"errors": ["not a dict"]})
        assert error_summary({})


class TestExceptionShapes:
    """Attributes consumers actually read."""

    def test_api_error_includes_status_in_its_message(self) -> None:
        assert "HTTP 404" in str(GiveBloodApiError(404, "not found"))

    def test_api_error_without_status_omits_the_prefix(self) -> None:
        assert str(GiveBloodApiError(None, "something")) == "something"

    def test_rate_limit_error_carries_retry_after(self) -> None:
        error = GiveBloodRateLimitError(429, "slow down", retry_after=30.0)
        assert error.retry_after == 30.0
        assert error.status == 429

    def test_booking_error_defaults_validation_errors_to_a_list(self) -> None:
        assert GiveBloodBookingError(400, "refused").validation_errors == []

    def test_booking_error_retains_validation_errors(self) -> None:
        errors = [{"errorCode": "NOT_BOOKABLE"}]
        assert GiveBloodBookingError(400, "refused", validation_errors=errors).validation_errors == errors

    def test_invalid_credentials_defaults(self) -> None:
        error = GiveBloodInvalidCredentialsError()
        assert error.reauth_required and not error.transient

    def test_token_expired_carries_the_wire_code(self) -> None:
        assert GiveBloodTokenExpiredError().code == "TOKEN_EXPIRED"
