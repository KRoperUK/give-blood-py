# Changelog

This file is maintained by [release-please](https://github.com/googleapis/release-please) from
Conventional Commit messages. Do not edit it by hand.

## [0.2.0](https://github.com/KRoperUK/give-blood-py/compare/v0.1.0...v0.2.0) (2026-09-15)


### Features

* async Python client for the NHS Give Blood donor API ([322fd24](https://github.com/KRoperUK/give-blood-py/commit/322fd243636a3a321ad6ceb0d05ad32e5b47f054))

## 0.1.0 (unreleased)

Initial release.

### Features

- Async client for the NHS Give Blood donor API, with a caller-owned `aiohttp` session.
- Token lifecycle handling: JWT expiry decoding, refresh-on-demand, password-login fallback, and an
  `on_token_update` callback for persistence.
- Read endpoints: account details, future appointments, donation history, awards, donor messages,
  feature flags, failover banner, version check, venue search, venue sessions, session slots.
- `async_get_snapshot()` aggregate read that degrades gracefully when supplementary endpoints fail.
- Write endpoints: book, reschedule (atomic) and cancel appointments. Never retried on ambiguous
  failure.
- Pydantic v2 models that normalise the API's null-date sentinel, naive local datetimes, string
  numerics and the `freeSlots: -1` "undisclosed" sentinel.
- Exception hierarchy with `reauth_required` / `transient` classification.
- `give-blood` CLI, with write commands gated behind `--yes`.
