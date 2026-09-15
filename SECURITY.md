# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for anything that could expose donor data or credentials.

Use GitHub's private reporting:
[Report a vulnerability](https://github.com/KRoperUK/give-blood-py/security/advisories/new)

Include what you can reproduce and what data it exposes. I'll acknowledge within a week.

## Scope

This project is an unofficial client for NHS Blood and Transplant's private API.

**In scope** — issues in this repository's code:

- credentials or donor data leaking into logs, exception messages, or exception `repr`
- PII reaching a committed file (fixtures, docs, examples)
- token handling flaws: tokens not rotated, refresh tokens logged, expiry checks bypassed
- authentication failures misclassified in a way that leaks or exposes credentials
- a dependency vulnerability reachable through this library

**Out of scope:**

- vulnerabilities in NHSBT's own systems. Report those to
  [NHS Blood and Transplant](https://www.nhsbt.nhs.uk/) directly, not here. Please do not test
  against their infrastructure on this project's behalf.
- the static application key in `const.py`. It is shipped in a publicly downloadable APK, identifies
  the *application* rather than any user, and authorises nothing on its own — the donor's Bearer
  token does all the authorisation. It is not a secret and there is no mechanism to supply it
  privately.

## What this library handles

It holds donor credentials and short-lived access tokens in memory, and its responses contain names,
addresses, phone numbers, dates of birth and donor identifiers. Design measures:

- `TokenBundle.__repr__` is redacted, so a token cannot reach a log line through an incidental `%s`
- API error summaries read only `errorCode` and `errorMessage`, never the `attemptedValue` field,
  which echoes back whatever the caller sent — on an auth endpoint that can be a password
- logins are never retried automatically; a retried login is a second password attempt against an
  account-lockout policy
- the aiohttp session is caller-owned, so the library never creates an unmanaged connection

## Repository hygiene

Four automated layers run on every commit and in CI to keep real donor data out of the repository:

| Layer | Catches |
|---|---|
| `scripts/check_pii.py` | Emails, UK postcodes, UK phone numbers, JWTs, Bearer tokens, NHSBT donor and donation IDs, credential assignments |
| `detect-secrets` | High-entropy secrets, against an audited baseline |
| `bandit` | Insecure code patterns |
| `tests/test_fixture_hygiene.py` | Non-synthetic values in test fixtures |

If you contribute a fixture, run it through `scripts/sanitise_capture.py` first and read the diff.

## Supported versions

The latest released version. This is a pre-1.0 project; fixes go into a new release rather than
being backported.
