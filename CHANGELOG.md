# Changelog

This file is maintained by [release-please](https://github.com/googleapis/release-please) from
Conventional Commit messages. Do not edit it by hand.

## [0.2.0](https://github.com/KRoperUK/give-blood-py/compare/v0.1.0...v0.2.0) (2026-09-15)


### Features

* live smoke tests and a completed read surface ([#19](https://github.com/KRoperUK/give-blood-py/issues/19)) ([a8c48ec](https://github.com/KRoperUK/give-blood-py/commit/a8c48ec4f5231a8cb79d8117d66ea439c3025bf7))

## [0.1.0](https://github.com/KRoperUK/give-blood-py/compare/v0.1.0...v0.1.0) (2026-09-15)


### Features

* async Python client for the NHS Give Blood donor API ([322fd24](https://github.com/KRoperUK/give-blood-py/commit/322fd243636a3a321ad6ceb0d05ad32e5b47f054))


### Bug Fixes

* **deps:** require pytest &gt;=9.0.3 ([4426fc4](https://github.com/KRoperUK/give-blood-py/commit/4426fc46b33d5d2bcfce5366746fbf8b4fcce0e4))
* stop the PII guard flagging retina asset filenames as email addresses ([71d89da](https://github.com/KRoperUK/give-blood-py/commit/71d89da6906b00dda9cba748a65c9c9c64f763b7))
* unblock the CI security and pre-commit jobs ([cf1d495](https://github.com/KRoperUK/give-blood-py/commit/cf1d495816a6cca0f2e7edafa0881aa1b1e27422))


### Miscellaneous Chores

* pin the first release to 0.1.0 ([2514e10](https://github.com/KRoperUK/give-blood-py/commit/2514e1071f3a2e76b4ef7eee7aff4ee08d33cf36))

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
