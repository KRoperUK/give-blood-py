# nhs-give-blood

Async Python client for the NHS Give Blood (NHSBT) donor API — the private API behind the official
[NHS Give Blood mobile app](https://www.blood.co.uk/nhsgivebloodapp).

[![PyPI](https://img.shields.io/pypi/v/nhs-give-blood)](https://pypi.org/project/nhs-give-blood/)
[![Python](https://img.shields.io/pypi/pyversions/nhs-give-blood)](https://pypi.org/project/nhs-give-blood/)
[![Licence](https://img.shields.io/badge/licence-MIT-blue)](LICENSE)

## What does this do?

It logs in as a blood donor and reads their account: eligibility dates, blood group, donation credits,
awards, upcoming appointments, donation history, and venue/session availability. It can also book,
reschedule and cancel appointments.

It powers [give-blood-hass](https://github.com/KRoperUK/give-blood-hass), a Home Assistant integration —
documented at [give-blood-hass.kroper.uk](https://give-blood-hass.kroper.uk).

**Unofficial.** NHSBT publishes no contract for these endpoints, does not support this client, and may
change or break it at any time. Endpoints and payloads here were recovered from the public Android
app (`com.savant.mobile.nhs.nhsgiveblood` 4.9.1) — see [docs/api-reference.md](docs/api-reference.md)
for the derivation, including how to reproduce the analysis.

## Contents

- [Install](#install)
- [Quick start](#quick-start)
- [Usage guide](#usage-guide)
- [API reference](#api-reference)
- [Data quirks worth knowing](#data-quirks-worth-knowing)
- [Booking, and why it needs care](#booking-and-why-it-needs-care)
- [CLI](#cli)
- [Troubleshooting](#troubleshooting)
- [Development](#development)

## Install

```bash
pip install nhs-give-blood
```

From source:

```bash
git clone https://github.com/KRoperUK/give-blood-py
cd give-blood-py
poetry install
```

Requires Python 3.12+.

## Quick start

```python
import asyncio
import aiohttp
from nhs_give_blood import GiveBloodClient

async def main() -> None:
    async with aiohttp.ClientSession() as session:
        client = GiveBloodClient(session, username="you@example.com", password="…")
        snapshot = await client.async_get_snapshot()

        account = snapshot.account
        print(f"{account.blood_group}, {account.donation_credit} credits")
        print(f"Eligible from {account.can_donate_from:%d %b %Y}")

        if appointment := snapshot.next_appointment:
            venue = appointment.venue.display_name if appointment.venue else "?"
            print(f"Next: {appointment.starts_at:%a %d %b %H:%M} at {venue}")

asyncio.run(main())
```

## Usage guide

### The session is yours

`GiveBloodClient` never creates or closes an `aiohttp.ClientSession`. Pass one in and own its
lifetime. In Home Assistant, pass `async_get_clientsession(hass)`.

### Authentication

The API issues a ~30-minute RS256 JWT access token plus an opaque refresh token. It sends no
`expires_in`; expiry is read from the token's own `exp` claim.

You can construct the client three ways:

```python
# Credentials only — logs in on first use.
GiveBloodClient(session, username=..., password=...)

# Stored tokens only — no password held in memory, but dies when the refresh token does.
GiveBloodClient(session, token_bundle=TokenBundle.from_mapping(saved))

# Both — the resilient option for long-running processes.
GiveBloodClient(session, username=..., password=..., token_bundle=...)
```

With both, the auth ladder is: reuse a fresh token → refresh → password login. NHSBT rotates refresh
tokens, so a stale stored one is routine rather than fatal; the fallback is what keeps an unattended
process alive across it.

Persist rotated tokens with the `on_token_update` callback (sync or async):

```python
def save(bundle: TokenBundle) -> None:
    Path("tokens.json").write_text(json.dumps(bundle.as_dict()))

client = GiveBloodClient(session, username=..., password=..., on_token_update=save)
```

`TokenBundle.as_dict()` / `.from_mapping()` are the storage contract — those field names are stable
public API.

### Reading

One aggregate read for everything a dashboard needs:

```python
snapshot = await client.async_get_snapshot()
```

`async_get_snapshot()` fetches the account payload (mandatory) and then, concurrently, appointments,
messages, feature flags, the failover banner and donation history. **Supplementary failures degrade
rather than propagate** — a flaky feature-flag endpoint leaves `snapshot.features is None` and adds
`"features"` to `snapshot.degraded`. Check `snapshot.partial` to tell a clean read from a degraded one.

Or call endpoints individually:

```python
account      = await client.async_get_account_details()
appointments = await client.async_get_future_appointments()
history      = await client.async_get_donation_history()
awards       = await client.async_get_awards()
messages     = await client.async_get_messages()
features     = await client.async_get_feature_flags(account.blood_group)
failover     = await client.async_get_failover()
```

### Finding somewhere to donate

```python
response = await client.async_search_venues("SW1A 1AA", procedure_code="WB")
for result in response.results:
    print(result.venue.display_name, result.venue_distance, result.date_of_next_session)

sessions = await client.async_get_sessions_at_venue("ABCD1", procedure_code="WB")
slots = await client.async_get_session_slots(
    sessions[0].session_id,
    session_date=sessions[0].session_date,
    start_time=sessions[0].periods[0].start_time,
    end_time=sessions[0].periods[0].end_time,
)
```

A 200 response with a non-empty `error_code` is a normal "no results, here's why" answer — check it
before treating an empty `results` list as an outage.

## API reference

### `GiveBloodClient`

| Method | Returns | Notes |
|---|---|---|
| `async_get_snapshot(include_donations=True)` | `DonorSnapshot` | Aggregate read; degrades gracefully |
| `async_get_account_details()` | `AccountDetails` | The widest payload in the API |
| `async_get_future_appointments()` | `list[Appointment]` | |
| `async_get_donation_history()` | `DonationHistory` | Truncated by the API; see `has_further_donations` |
| `async_get_awards()` | `AwardsData` | |
| `async_get_messages()` | `MessageBundle` | Returns all blood groups; filter client-side |
| `async_get_feature_flags(blood_group=None)` | `FeatureFlags` | Varies by client version |
| `async_get_failover()` | `FailoverBanner` | `is_active` means booking is down |
| `async_get_version_check(platform, version)` | `VersionCheck` | |
| `async_search_venues(search_criteria, …)` | `VenueSearchResponse` | Postcode or place name |
| `async_get_sessions_at_venue(venue_id, …)` | `list[Session]` | |
| `async_get_session_slots(session_id, …)` | `SessionSlots` | |
| `async_validate_token()` | `bool` | False on rejection; raises on unreachable |
| `async_login()` | `AccountDetails \| None` | Login already embeds the account |
| `async_logout()` | `None` | |
| `async_book_appointment(…)` | `dict` | **Real-world write** |
| `async_reschedule_appointment(…)` | `dict` | **Real-world write**, atomic |
| `async_cancel_appointment(appointment_id)` | `None` | **Real-world write**, irreversible |

### Exceptions

All inherit `GiveBloodError`.

| Exception | When | Consumer should |
|---|---|---|
| `GiveBloodConnectionError` | Host unreachable, socket died, timeout | Retry later |
| `GiveBloodApiError` | Unexpected non-2xx | Inspect `.status` |
| `GiveBloodRateLimitError` | HTTP 429 | Back off; honour `.retry_after` |
| `GiveBloodAuthError` | Auth failed | Branch on `.reauth_required` / `.transient` |
| `GiveBloodInvalidCredentialsError` | Username/password rejected | Prompt for new credentials |
| `GiveBloodTokenExpiredError` | Token dead and unrefreshable | Prompt for new credentials |
| `GiveBloodBookingError` | Booking refused | Show `.validation_errors` |

Branch on the booleans, not the class:

```python
except GiveBloodAuthError as err:
    if err.transient:
        raise UpdateFailed("Auth service down, will retry") from err
    raise ConfigEntryAuthFailed("Credentials no longer valid") from err
```

## Data quirks worth knowing

These bite anyone reading the raw API. The models normalise all of them.

- **`0001-01-01T00:00:00` is null.** .NET's `DateTime.MinValue`, used instead of `null` for absent
  dates. `parse_api_datetime` returns `None`.
- **Datetimes are naive but not UTC.** They are venue-local wall-clock time. Every datetime this
  library returns is timezone-aware in `Europe/London`.
- **`freeSlots: "-1"` is not zero.** It is a string, and -1 means "not disclosed". Check
  `Period.has_known_free_slots` before trusting the number.
- **Two clock formats.** Appointments use `THHMM`, session periods use bare `HHMM`.
- **A session date is never a start time.** Its time component is always midnight; the appointment's
  own `time` field supplies the clock. `Appointment.starts_at` combines them.
- **Two procedure-code vocabularies.** An appointment's platelet code is `PLT`; the donor's own
  registered `procedureCode` for the same thing is `PL1`. Never compare one to the other.
- **Donation history is truncated.** `hasFurtherDonations` tells you so. The list length is a lower
  bound; `AccountDetails.donation_credit` is the authoritative lifetime count.
- **Messages arrive for every blood group.** Filter with `MessageBundle.for_blood_group()`.
- **Donation `type` codes are undocumented.** A/B/L/R observed. NHSBT publishes no mapping, so the
  code is passed through verbatim rather than guessed at.
- **Unknown fields are kept, not rejected.** Models use `extra="allow"`, so a new API field cannot
  break parsing; it lands in `model_extra`.

## Booking, and why it needs care

`async_book_appointment`, `async_reschedule_appointment` and `async_cancel_appointment` change a real
appointment at a real NHS clinic. A cancelled slot is released immediately and may be taken by someone
else; a wasted slot is a wasted donation.

Two deliberate design choices follow from that:

- **Writes are never retried** after an ambiguous failure (timeout, 5xx). A replayed booking POST
  could create a duplicate appointment, which is worse than a reported failure.
- **Rescheduling is one atomic call**, not cancel-then-book, which can lose the slot in between.

Check `async_get_failover().is_active` before a write: when NHSBT has the booking system down, writes
fail.

## CLI

```bash
export NHS_GIVE_BLOOD_EMAIL="you@example.com"
export NHS_GIVE_BLOOD_PASSWORD="…"

give-blood whoami           # account, credits, eligibility
give-blood appointments     # upcoming
give-blood history          # past donations
give-blood snapshot         # everything, as JSON
give-blood venues "SW1A 1AA"
give-blood sessions ABCD1
```

Write commands refuse to run without `--yes`:

```bash
give-blood cancel APPT123 --yes
```

Prefer environment variables over `--password`: shell history is not a secret store. A local `.env` is
read automatically when `python-dotenv` is installed.

## Troubleshooting

**`GiveBloodInvalidCredentialsError` on login.** Check the credentials in the app first. NHSBT locks
accounts after repeated failures, and this client deliberately does not retry logins, so a lockout
here means something else is retrying.

**`GiveBloodTokenExpiredError` in a long-running process.** The stored refresh token was rejected and
there were no credentials to fall back on. Construct the client with both.

**Auth failures that come and go.** Check `err.transient`. A 5xx or 429 from the auth service is
transient; treating it as a credential problem will produce spurious reauth prompts.

**Empty venue search results.** Read `response.error_code` — the API answers "no sessions in range"
with HTTP 200 and an error code, not an empty list alone.

**Bookings failing with a validation error.** Read `err.validation_errors`. The commonest causes are a
`startTime`/`endTime` pair the API did not advertise for that session, and a clashing existing
appointment (`SessionSlots.clashing_appointments`).

**Everything failing at once.** Check `async_get_failover()`. NHSBT takes the booking system down for
maintenance and the app shows a banner rather than an error.

## Development

```bash
poetry install
poetry run pytest
poetry run ruff check . && poetry run ruff format --check .
poetry run mypy
pre-commit install
```

Tests never touch the network: `aresponses` mocks the API and every fixture under `tests/fixtures/` is
a sanitised capture containing only synthetic identities.

**If you re-capture from a live account**, run it through the sanitiser before it goes anywhere near
git:

```bash
python scripts/sanitise_capture.py /tmp/capture.json tests/fixtures/thing.json
```

`tests/test_fixture_hygiene.py`, `scripts/check_pii.py` (pre-commit), bandit and detect-secrets all
guard this, but review the diff yourself too. Live responses from this API are saturated with donor
PII.

## Licence

MIT. Not affiliated with, endorsed by, or supported by NHS Blood and Transplant.

## Releasing

Releases are automated. A Conventional Commit on `main` makes
[release-please](https://github.com/googleapis/release-please) open a release pull request; merging it
tags the version, writes the changelog, and publishes to PyPI via
[trusted publishing](https://docs.pypi.org/trusted-publishers/) — no API token is stored in this
repository.

One-time PyPI setup, if this is ever re-established from scratch:

1. On PyPI, go to **Your projects → Publishing** (or, before the first release,
   **Account settings → Publishing → Add a pending publisher**).
2. Enter exactly:

   | Field | Value |
   |---|---|
   | PyPI project name | `nhs-give-blood` |
   | Owner | `KRoperUK` |
   | Repository name | `give-blood-py` |
   | Workflow name | `release-please.yml` |
   | Environment name | `pypi` |

The `pypi` GitHub environment restricts deployments to `main` and `v*` tags, so a fork or a feature
branch cannot publish.
