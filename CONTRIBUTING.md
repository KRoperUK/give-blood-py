# Contributing

## Setup

```bash
poetry install
pre-commit install          # installs both pre-commit and pre-push hooks
```

## The one rule that matters most

**Never commit real donor data.** Every account endpoint in this API returns the donor's name, address,
phone number, date of birth and donor ID, and an access token in a fixture is a live credential.

Four layers guard this, and all four run automatically:

| Layer | Catches |
|---|---|
| `scripts/check_pii.py` (pre-commit + CI) | Emails, UK postcodes, UK phone numbers, JWTs, Bearer tokens, NHSBT donor/donation IDs, credential assignments |
| `detect-secrets` (pre-commit + CI) | High-entropy secrets, against an audited `.secrets.baseline` |
| `bandit` (pre-commit + CI) | Insecure code patterns in the library |
| `tests/test_fixture_hygiene.py` | Non-synthetic values in `tests/fixtures/` |

If you capture from a live account, run it through the sanitiser first:

```bash
python scripts/sanitise_capture.py /tmp/capture.json tests/fixtures/thing.json
```

Then read the diff. The scanners are a safety net, not a substitute for looking.

The reserved substitutes to use in tests and docs:

| Kind | Value | Why it's safe |
|---|---|---|
| Email | `donor@example.invalid` | RFC 6761 reserves `.invalid` |
| Postcode | `SW1A 1AA`, or any `ZZ99 …` | Royal Mail reserves the `ZZ99` outcode |
| Phone | `07700900000` | Ofcom's reserved drama range |
| Donor ID | `D0000000` | |
| Donation ID | `G000000000000X` | |
| Token | anything starting `synthetic` | |

If a finding is genuinely safe, add a `pii-allow` comment to that line — and say why.

## Tests

```bash
poetry run pytest                      # unit tests; no network
poetry run pytest -m live              # hits the real API; needs .env credentials
```

Unit tests must never touch the network. `aresponses` mocks the API, and clients are constructed with
`base_url` pointing at `test.invalid` so a missing mock fails loudly instead of reaching NHSBT.

Live tests are marked `live` and deselected by default.

## Checks

```bash
poetry run ruff check . && poetry run ruff format --check .
poetry run mypy
poetry run bandit -c bandit.yaml -r nhs_give_blood
poetry run pytest --cov
```

Coverage floor is 80%. Ruff's version is pinned in both `pyproject.toml` and
`.pre-commit-config.yaml`; CI resolves it from the pre-commit file, so bump both together.

## Commits and releases

Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`). release-please reads
them to decide the version bump, write the changelog, and publish to PyPI via trusted publishing. Do
not bump the version by hand.

## Adding an endpoint

1. Add the path to `const.py`.
2. Add or extend a model in `models.py`. Prefer `extra="allow"` behaviour (inherit `_Base`) so a future
   API field can't break parsing.
3. Add the client method. Reads may retry; **writes must not** — a replayed booking POST could create a
   duplicate NHS appointment.
4. Add a sanitised fixture and a test.
5. Document the endpoint in `docs/api-reference.md`, including any wire quirks. Undocumented quirks are
   how the next person loses a day.

## Writes

`async_book_appointment`, `async_reschedule_appointment` and `async_cancel_appointment` change a real
appointment at a real NHS clinic. Treat changes to them with proportionate care: no automatic retries,
no convenience wrapper that could fire one unintentionally, and no speculative calls in tests against a
live account.
