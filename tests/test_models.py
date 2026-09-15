"""Model parsing tests, driven by sanitised captures of real responses."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from nhs_give_blood import (
    AccountDetails,
    Appointment,
    AwardsData,
    DonationHistory,
    MessageBundle,
    VenueSearchResponse,
    parse_api_datetime,
    parse_wire_time,
)
from nhs_give_blood.models import Period, Session, VenueSummary

from .fixtures import load_fixture

LONDON = ZoneInfo("Europe/London")


class TestDateTimeParsing:
    """The wire format's two traps: a null sentinel and naive local times."""

    def test_null_sentinel_becomes_none(self) -> None:
        assert parse_api_datetime("0001-01-01T00:00:00") is None

    def test_empty_and_none_become_none(self) -> None:
        assert parse_api_datetime("") is None
        assert parse_api_datetime(None) is None

    def test_garbage_becomes_none_rather_than_raising(self) -> None:
        assert parse_api_datetime("not a date") is None
        assert parse_api_datetime(12345) is None

    def test_naive_datetime_is_localised_to_venue_timezone(self) -> None:
        parsed = parse_api_datetime("2026-10-08T00:00:00")
        assert parsed == datetime(2026, 10, 8, tzinfo=LONDON)
        assert parsed is not None and parsed.tzinfo is not None

    def test_aware_datetime_is_left_alone(self) -> None:
        parsed = parse_api_datetime("2026-10-08T09:30:00+00:00")
        assert parsed is not None
        assert parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0


class TestWireTimeParsing:
    """Appointments use ``THHMM``; session periods use bare ``HHMM``."""

    def test_appointment_prefix_form(self) -> None:
        assert parse_wire_time("T1730") == time(17, 30)

    def test_bare_form(self) -> None:
        assert parse_wire_time("1130") == time(11, 30)

    def test_invalid_forms_return_none(self) -> None:
        for value in (None, "", "17:30", "T173", "9999", "abcd"):
            assert parse_wire_time(value) is None


class TestAccountDetails:
    """The widest response in the API."""

    def test_parses_capture(self) -> None:
        account = AccountDetails.model_validate(load_fixture("account_details"))
        assert account.blood_group
        assert account.donation_credit >= 0
        assert account.eligibility is not None
        assert account.awards_data is not None

    def test_eligibility_dates_are_aware(self) -> None:
        account = AccountDetails.model_validate(load_fixture("account_details"))
        assert account.can_donate_from is not None
        assert account.can_donate_from.tzinfo is not None

    def test_full_name_joins_available_parts(self) -> None:
        account = AccountDetails.model_validate(load_fixture("account_details"))
        assert account.full_name == "Ada Testerson"

    def test_full_name_is_none_when_nothing_populated(self) -> None:
        assert AccountDetails.model_validate({}).full_name is None

    def test_home_address_prefers_type_h(self) -> None:
        account = AccountDetails.model_validate(
            {"addresses": [{"type": "W", "postcode": "ZZ99 3WZ"}, {"type": "H", "postcode": "ZZ99 3HZ"}]}
        )
        assert account.home_address is not None
        assert account.home_address.postcode == "ZZ99 3HZ"

    def test_home_address_falls_back_to_first(self) -> None:
        account = AccountDetails.model_validate({"addresses": [{"type": "W", "postcode": "ZZ99 3WZ"}]})
        assert account.home_address is not None
        assert account.home_address.postcode == "ZZ99 3WZ"

    def test_next_appointment_picks_soonest(self) -> None:
        account = AccountDetails.model_validate(load_fixture("account_details"))
        appointment = account.next_appointment
        assert appointment is not None
        others = [a for a in account.appointments if a.starts_at is not None]
        assert appointment.starts_at == min(a.starts_at for a in others if a.starts_at)

    def test_next_appointment_ignores_cancelled(self) -> None:
        account = AccountDetails.model_validate(
            {
                "appointments": [
                    {"time": "T0900", "cancellationCode": "X", "session": {"sessionDate": "2026-10-01T00:00:00"}},
                    {"time": "T1000", "session": {"sessionDate": "2026-10-05T00:00:00"}},
                ]
            }
        )
        assert account.next_appointment is not None
        assert account.next_appointment.time == "T1000"

    def test_unknown_fields_are_retained_not_rejected(self) -> None:
        """A new API field must not break parsing."""
        account = AccountDetails.model_validate({"bloodGroup": "O-", "someBrandNewField": 42})
        assert account.blood_group == "O-"
        assert account.model_extra is not None
        assert account.model_extra["someBrandNewField"] == 42


class TestAppointment:
    """Start time is assembled from two separate wire fields."""

    def test_start_time_combines_session_date_and_clock(self) -> None:
        appointment = Appointment.model_validate({"time": "T1730", "session": {"sessionDate": "2026-10-08T00:00:00"}})
        assert appointment.starts_at == datetime(2026, 10, 8, 17, 30, tzinfo=LONDON)

    def test_missing_clock_falls_back_to_session_date(self) -> None:
        appointment = Appointment.model_validate({"session": {"sessionDate": "2026-10-08T00:00:00"}})
        assert appointment.starts_at == datetime(2026, 10, 8, tzinfo=LONDON)

    def test_missing_session_yields_no_start_time(self) -> None:
        assert Appointment.model_validate({"time": "T1730"}).starts_at is None

    def test_capture_parses_and_exposes_venue(self) -> None:
        appointments = [Appointment.model_validate(item) for item in load_fixture("appointments_future")]
        assert appointments
        assert appointments[0].venue is not None
        assert appointments[0].session_id
        assert not appointments[0].is_cancelled


class TestSessionAndPeriod:
    """Free-slot counts carry a sentinel that must not be read as zero."""

    def test_sentinel_free_slots_are_flagged_unknown(self) -> None:
        period = Period.model_validate({"freeSlots": "-1", "availableSlots": 0})
        assert period.free_slots == -1
        assert not period.has_known_free_slots

    def test_real_free_slots_are_known(self) -> None:
        period = Period.model_validate({"freeSlots": "4"})
        assert period.has_known_free_slots
        assert period.free_slots == 4

    def test_total_free_slots_is_none_when_all_undisclosed(self) -> None:
        session = Session.model_validate({"periods": [{"freeSlots": "-1"}, {"freeSlots": "-1"}]})
        assert session.total_free_slots is None

    def test_total_free_slots_sums_disclosed_only(self) -> None:
        session = Session.model_validate({"periods": [{"freeSlots": "-1"}, {"freeSlots": "3"}, {"freeSlots": "2"}]})
        assert session.total_free_slots == 5

    def test_status_combo_matches_app_construction(self) -> None:
        session = Session.model_validate(
            {"sessionStatus": "C", "appointmentStatus": "Y", "availability": "Y", "bookingFlag": "Y"}
        )
        assert session.status_combo == "CYYY"

    def test_status_combo_pads_missing_parts(self) -> None:
        assert Session.model_validate({"sessionStatus": "C"}).status_combo == "C---"

    def test_period_clock_accessors(self) -> None:
        period = Period.model_validate({"startTime": "1130", "endTime": "1500"})
        assert period.opens_at == time(11, 30)
        assert period.closes_at == time(15, 0)


class TestVenueSummary:
    """Display naming and coordinate coercion."""

    def test_display_name_appends_internal_location(self) -> None:
        venue = VenueSummary.model_validate({"venueName": "Town Hall", "internalLocation": "Main Hall"})
        assert venue.display_name == "Town Hall (Main Hall)"

    def test_display_name_avoids_duplicating_location(self) -> None:
        venue = VenueSummary.model_validate({"venueName": "Town Hall Main Hall", "internalLocation": "Main Hall"})
        assert venue.display_name == "Town Hall Main Hall"

    def test_string_coordinates_become_floats(self) -> None:
        venue = VenueSummary.model_validate({"latitude": "51.5", "longitude": "-0.12"})
        assert venue.latitude == 51.5
        assert venue.longitude == -0.12

    def test_blank_coordinates_become_none(self) -> None:
        venue = VenueSummary.model_validate({"latitude": "", "longitude": None})
        assert venue.latitude is None
        assert venue.longitude is None

    def test_address_one_line(self) -> None:
        venue = VenueSummary.model_validate(
            {"address": {"lines": ["1 Example Street", "Testville"], "postcode": "SW1A 1AA"}}
        )
        assert venue.address is not None
        assert venue.address.one_line == "1 Example Street, Testville, SW1A 1AA"


class TestAwards:
    """Next-award maths is what drives the "N credits to go" sensor."""

    def test_parses_capture(self) -> None:
        awards = AwardsData.model_validate(load_fixture("awards"))
        assert awards.total_credits >= 0
        assert awards.awards

    def test_next_award_is_nearest_unachieved(self) -> None:
        awards = AwardsData.model_validate(
            {
                "totalCredits": 15,
                "awards": [
                    {"isAchieved": True, "title": "Bronze", "creditCriteria": 10},
                    {"isAchieved": False, "title": "Silver", "creditCriteria": 25},
                    {"isAchieved": False, "title": "Gold", "creditCriteria": 50},
                ],
            }
        )
        assert awards.next_award is not None
        assert awards.next_award.title == "Silver"
        assert awards.credits_to_next_award == 10

    def test_credits_to_next_award_floors_at_zero(self) -> None:
        awards = AwardsData.model_validate(
            {"totalCredits": 30, "awards": [{"isAchieved": False, "title": "Silver", "creditCriteria": 25}]}
        )
        assert awards.credits_to_next_award == 0

    def test_no_pending_awards_yields_none(self) -> None:
        awards = AwardsData.model_validate({"awards": [{"isAchieved": True, "creditCriteria": 1}]})
        assert awards.next_award is None
        assert awards.credits_to_next_award is None

    def test_next_tier_skips_credit_count_awards(self) -> None:
        awards = AwardsData.model_validate(
            {
                "totalCredits": 15,
                "awards": [
                    {"isAchieved": False, "title": "20 Credits", "creditCriteria": 20},
                    {"isAchieved": False, "title": "Silver", "creditCriteria": 25},
                ],
            }
        )
        assert awards.next_award is not None and awards.next_award.title == "20 Credits"
        assert awards.next_tier is not None and awards.next_tier.title == "Silver"


class TestDonationHistory:
    """Truncation is signalled, not implied by list length."""

    def test_parses_capture(self) -> None:
        history = DonationHistory.model_validate(load_fixture("donation_history"))
        assert history.donation
        assert history.most_recent is not None
        assert history.most_recent.donated_at is not None

    def test_most_recent_is_latest_by_date(self) -> None:
        history = DonationHistory.model_validate(
            {
                "donation": [
                    {"donationId": "A", "session": {"sessionDate": "2024-01-01T00:00:00"}},
                    {"donationId": "B", "session": {"sessionDate": "2026-01-01T00:00:00"}},
                ]
            }
        )
        assert history.most_recent is not None
        assert history.most_recent.donation_id == "B"

    def test_undated_donations_are_ignored_not_fatal(self) -> None:
        history = DonationHistory.model_validate({"donation": [{"donationId": "A"}]})
        assert history.most_recent is None


class TestMessages:
    """Messages arrive for every blood group and must be filtered client-side."""

    def test_parses_capture(self) -> None:
        bundle = MessageBundle.model_validate(load_fixture("messages")["messages"])
        assert bundle.donor_messages

    def test_filters_by_blood_group(self) -> None:
        bundle = MessageBundle.model_validate(
            {
                "donorMessages": [
                    {"id": "1", "bloodGroups": ["O-"]},
                    {"id": "2", "bloodGroups": ["A-"]},
                    {"id": "3", "bloodGroups": ["All"]},
                ]
            }
        )
        assert [m.id for m in bundle.for_blood_group("A-")] == ["2", "3"]

    def test_message_with_no_groups_applies_to_everyone(self) -> None:
        from nhs_give_blood import DonorMessage

        assert DonorMessage.model_validate({}).applies_to(None)


class TestVenueSearch:
    """A 200 with an error code is a normal "no results" answer."""

    def test_parses_capture(self) -> None:
        response = VenueSearchResponse.model_validate(load_fixture("venues"))
        assert response.results
        assert response.results[0].venue is not None

    def test_null_next_session_sentinel_is_none(self) -> None:
        response = VenueSearchResponse.model_validate({"results": [{"dateOfNextSession": "0001-01-01T00:00:00"}]})
        assert response.results[0].date_of_next_session is None
