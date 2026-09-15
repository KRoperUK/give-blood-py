# NHS Give Blood app API — reference

Everything below was recovered from the public NHS Give Blood Android app and then verified against the
live API with a single donor account. It is not an official contract; NHSBT may change any of it.

- **Package**: `com.savant.mobile.nhs.nhsgiveblood`
- **Version analysed**: 4.9.1
- **Stack**: React Native (new architecture) with Hermes bytecode v96
- **Host**: `https://my.blood.co.uk`

## How this was derived

The APK is a split bundle (`.xapk`): a base APK plus `config.arm64_v8a` and `config.mdpi`. The
application logic is not in the DEX files — it is JavaScript compiled to Hermes bytecode at
`assets/index.android.bundle` (7.8 MB). Native libraries in the arm64 split
(`libreactnative.so`, `libhermesvm.so`, `libqueueit_*.so`) confirm the stack.

```bash
unzip -q NHS+Give+Blood_4.9.1.xapk -d xapk
unzip -q xapk/com.savant.mobile.nhs.nhsgiveblood.apk -d base
file base/assets/index.android.bundle      # Hermes JavaScript bytecode, version 96
hbc-decompiler base/assets/index.android.bundle bundle.js
```

`hbc-decompiler` (from [hermes-dec](https://github.com/P1sec/hermes-dec)) produces ~48 MB of
register-level pseudo-JavaScript. Raw `strings` output is not enough on its own: Hermes packs its
string table without separators, so adjacent literals run together and endpoint paths get mangled.
Decompiling recovers correct string boundaries and, more usefully, the object literals that hold the
route table.

The route table is a single module-level object; the axios instance configuration (base URL, standing
headers, interceptors) is another. Those two objects are the whole API surface.

## Transport

### Standing headers

Every request the app makes to the API host carries:

| Header | Value | Why it matters |
|---|---|---|
| `ApiKey` | `b0046936-5a05-439e-8a89-5beab70829b7` | Static app key. Requests without it fail. |
| `Nhsbt-Client-Type` | `app` (or `web`) | Feature flags and the failover banner vary by this. |
| `Nhsbt-Client-Version` | `4.9.1` | Version-gated responses. |
| `Cache-Control` | `no-cache, no-store` | Intermediary caches otherwise return stale bodies. |
| `Pragma` | `no-cache` | As above. |
| `Expires` | `0` | As above. |
| `Authorization` | `Bearer <accessToken>` | Added by an interceptor once signed in. |

The `ApiKey` interceptor is conditional: it only fires when the request's `baseURL` matches the
configured default, so third-party hosts (Google Maps geocoding, Azure Application Insights) never
receive it.

On the key itself: it is a fixed value shipped in a publicly downloadable APK, it identifies the
application rather than any user, and it authorises nothing on its own — the donor Bearer token does
all the authorisation. This library hardcodes it in `const.py` because there is no configuration path
that could supply it and the client cannot function without it.

### Authentication

`POST /api/auth/v2/login`

```json
{
  "username": "donor@example.invalid",
  "password": "…",
  "plasmaLoginAllowed": true,
  "biometricSignature": null
}
```

Returns `{ "accessToken": …, "refreshToken": …, "accountDetails": { … } }`. The embedded
`accountDetails` is byte-for-byte what `/api/account/v2/details` returns, so a login needs no
follow-up read.

The access token is an RS256 JWT with a ~30 minute lifetime and three claims beyond the standard set:

| Claim | Meaning |
|---|---|
| `exp` | Expiry. **The only source of expiry** — the API sends no `expires_in`. |
| `donor_id` | Stable donor identifier. Useful as an account key without an extra request. |
| `user_id` | Internal account identifier. |
| `can_book` | Whether the account may book at all. |

The refresh token is an opaque 88-character string, not a JWT.

### Token refresh

The app's response interceptor treats **HTTP 401 with `body.code == "TOKEN_EXPIRED"`** as "refresh and
replay":

```
POST /api/auth/refresh   { "refreshToken": … }  ->  { "accessToken": …, "refreshToken": … }
```

Then the original request is retried. A 401 *without* that code means the credentials are genuinely
dead. The app serialises concurrent refreshes behind a mutex and an `unlocked` event so a burst of
requests triggers one refresh, not several.

Refresh tokens rotate. A stored one going stale is routine, which is why this library falls back from a
failed refresh to a password login rather than surfacing an error.

### Other auth endpoints

| Method | Path | Body |
|---|---|---|
| `POST` | `/api/auth/logout` | `{refreshToken}` |
| `GET` | `/api/auth/validate` | — (**retired**: the live service answers 404, and 500 for a POST, despite the route still being in the app bundle) |
| `POST` | `/api/auth/change-password` | — |
| `POST` | `/api/auth/reset/password` | — |
| `POST` | `/api/auth/request/username` | — |
| `POST` | `/api/auth/request/password-reset` | — |
| `POST` | `/api/auth/biometrics/enrol` | — |
| `POST` | `/api/auth/biometrics/withdraw` | — |
| `GET` | `/api/auth/token/{token}` | — |

## Endpoints

### Account

| Method | Path | Returns |
|---|---|---|
| `GET` | `/api/account/v2/details` | The full donor payload (below) |
| `GET` | `/api/account/donation-history` | `{donorID, hasFurtherDonations, donation[]}` |
| `GET` | `/api/account/awards` | `awardsData` |
| `POST` | `/api/account/award` | Acknowledge an award |
| `POST` | `/api/account/email/request` | Start an email change |
| `POST` | `/api/account/email/verify` | Complete an email change |
| `GET` | `/api/account/certificate` | Donation certificate |

`/api/account/v2/details` is the widest response in the API. Notable groups:

- `eligibility` — `nextPossibleDonationDate` (clinical deferral expiry) and
  `nextPossibleAppointmentDate` (also accounts for booking windows and existing appointments). **These
  differ**, and conflating them is the easiest mistake to make here.
- `searchDatesFrom` — earliest searchable date per procedure (`wholeBlood`, `plasma`, `donationIntent`).
- `bloodGroup`, `serology{shortHand, roEnabled}` — group and antigen detail.
- `donationCredit` — lifetime credits. Authoritative, unlike the length of the donation-history list.
- `procedureType` / `procedureCode` / `procedureDescription` — the donor's registered donation type.
- `awardsData` — `{registrationDate, awardState, totalCredits, totalAwards, awards[], highestAchieved}`.
- `appointments[]` — same shape as `/api/appointments/future`.
- `nearestPlasmaVenue` — a venue plus `venueDistance`, `dateOfNextSession`, `sessionDayCount`.
- `venues[]` — the donor's preferred venues. **A different shape to `VenueSummary`**: the identifier
  is `venueID` with a capital `ID` rather than `venueId`, and there are two fields found nowhere else
  — `venPref` (a preference code) and `gridReference`. There are no `is*Supported` flags. This is why
  it is modelled as its own type rather than aliased onto the venue model used everywhere else.
- `showBookingCTA`, `hasPreviousDonations`, `isPlateletPlus`, `emailChangePending`,
  `referToCallCentre`, `lastDonatedVenueId`.
- Directly identifying: `donorID`, `title`, `forenames`, `surname`, `addresses[]`, `telephones[]`,
  `emails[]`, `dateOfBirth`.

### Appointments

| Method | Path | Params / body |
|---|---|---|
| `GET` | `/api/appointments/future` | — |
| `POST` | `/api/appointments/book` | `{sessionID, sessionDate, sessionTime, venueId, procedureCode}` |
| `POST` | `/api/appointments/replace` | `{newAppointment{sessionID,sessionDate,sessionTime,venueId}, oldAppointment{…}, procedureCode}` |
| `DELETE` | `/api/appointments/{id}` | — |
| `GET` | `/api/appointments/{sessionId}/slots` | `sessionDate`, `startTime`, `endTime`, `procedureCode` |

Note the inconsistent casing: booking payloads use `sessionID`, everything else uses `sessionId`.

The slots response includes `clashingAppointments` — the donor's existing appointments in the same
window. This is why a slot can look bookable and still be refused. Each entry in `slots[]` looks
like:

```json
{
  "time": "T1730",
  "procedureCode": "WB",
  "procedureType": "WholeBlood",
  "procedureDescription": "Whole Blood",
  "lastOneAvailable": false
}
```

`time` is the value to pass back as `sessionTime` when booking, in the same `THHMM` form as an
appointment's `time`. A period advertising a free slot can still return an empty `slots[]` — a window
with one free slot frequently yields none, because the slot is only offered when it is genuinely
bookable at that moment.

An appointment looks like:

```json
{
  "status": "A",
  "time": "T1730",
  "procedureCode": "PLT",
  "procedureType": "Platelet",
  "procedureDescription": "Platelet",
  "cancellationCode": null,
  "session": {
    "sessionId": "CS0000",
    "sessionDate": "2026-10-08T00:00:00",
    "venue": { "…": "…" },
    "periods": [{"startTime": "1130", "endTime": "1500", "freeSlots": "-1", "availableSlots": 0}],
    "sessionStatus": "C",
    "bookingFlag": "Y",
    "appointmentStatus": "Y",
    "availability": "Y",
    "weightingFactor": null,
    "notes": []
  }
}
```

### Venues and sessions

| Method | Path | Params |
|---|---|---|
| `GET` | `/api/venues` | `searchCriteria`, `startDate`, `endDate`, `procedureType`, `procedureCode`, `venueId`, `homeLatitude`, `homeLongitude`, `includeFullyBookedSessions` |
| `GET` | `/api/sessions/{venueId}` | `startDate`, `endDate`, `procedureCode`, `includeFullyBookedSessions` |
| `GET` | `/api/sessions/par` | Predictive appointment request sessions |
| `GET` | `/api/locations` | — |
| `GET` | `/api/address-search` | `postcode` |

`searchCriteria` takes a postcode or a place name. `/api/venues` returns
`{status, results[], potentialLocations, errorCode, errorInformation, startDateOffsetMonths,
maxSearchDistance, furthestVenueDistance, nearestPlasmaVenue, count}`. A **200 with a non-empty
`errorCode`** is the normal "no sessions in range" answer — an empty `results` list alone does not
distinguish that from an outage.

`potentialLocations` is a list of **plain strings**, not objects — display labels for a place name
that matched several towns, e.g. `"NEWPORT (GWENT)"`, `"NEWPORT (ISLE OF WIGHT)"`. The app renders
them into a disambiguation list verbatim. Typing them as objects (as this library first did) makes an
ambiguous search raise a `ValidationError` instead of returning the list the API sent.

`/api/address-search` takes `postcode` — **not** the `searchCriteria` its sibling endpoints use.
Passing `searchCriteria` returns `400 SHOULD_NOT_BE_EMPTY` naming a property called `Postcode`, which
is how the parameter was identified. The response is a bare JSON array with no envelope, and each
entry has the same shape as the addresses elsewhere in the API (`type`, `companyName`, `lines`,
`postcode`, `latitude`, `longitude`). The app uses it to turn a postcode into a selectable address
during sign-up.

Sessions returned by `/api/sessions/{venueId}` have `venue: null`; the caller already knows the venue.

Booleans in query strings must be lowercase — ASP.NET model binding rejects `True`/`False`.

### Content and configuration

| Method | Path | Params | Returns |
|---|---|---|---|
| `GET` | `/api/messages` | — | `{messages:{donorMessages[], endOfBooking[], endOfSignUp[]}}` |
| `GET` | `/api/features` | `platform`, `version`, `bloodGroup`, … | Feature flags |
| `GET` | `/api/features/failover` | `platform=web`, `version` | `{header, content, isActive}` |
| `GET` | `/api/app/version-check/{platform}/{version}` | — | `{newVersionAvailable, forceUpdateRequired, updateMessage, updateMessageWithLink, updateMessageLink}` |
| `GET` | `/api/media-services/v2/videos/metadata` | — | Video metadata |
| `GET` | `/api/knowledgebase/articles/{health\|travel}` | `criteria` | Eligibility articles |
| `GET` | `/api/knowledgebase/article/{health\|travel}` | `id`, `title` | One article |

Feature flags observed: `appointmentRequestBetaBanner`, `chatbot`, `checkIn`, `failover`,
`parFullSessions`, `waitingList`.

`/api/features` **requires** `platform`; omitting it returns 400 `PLATFORM_EMPTY`.
`/api/features/failover` is queried by the app with `platform=web`, not `app`.

`/api/messages` returns messages for **every** blood group, each tagged with a `bloodGroups` array
(`["All"]` for universal). Filtering is the client's job.

### Web-only routes

`/api/questionnaire*` and `/api/chat*` appear in the app's route table but return the site's Next.js
HTML 404 page when called with app credentials. They are reachable only from the web front end.

## Error shapes

Two envelopes:

```json
{ "message": "Human readable" }
```

```json
{
  "message": "The request was invalid",
  "errors": [
    {
      "propertyName": "Platform",
      "errorMessage": "'Platform' must not be empty.",
      "attemptedValue": null,
      "errorCode": "PLATFORM_EMPTY",
      "severity": 0,
      "formattedMessagePlaceholderValues": { "…": "…" }
    }
  ]
}
```

The second is FluentValidation. **`attemptedValue` echoes back whatever the caller sent**, which on an
auth endpoint means it can contain a password — so it must never be logged. `exceptions.error_summary`
deliberately reads only `errorCode` and `errorMessage`.

## Data quirks

| Quirk | Detail |
|---|---|
| Null dates | `0001-01-01T00:00:00` (.NET `DateTime.MinValue`) instead of `null`. |
| Naive local times | Datetimes have no offset and are venue-local (Europe/London), not UTC. |
| Session dates carry no time | Always midnight; the appointment's `time` supplies the clock. |
| Two clock formats | `THHMM` on appointments, bare `HHMM` on session periods. |
| `freeSlots` is a string | And `-1` means "not disclosed", not zero. |
| Two procedure vocabularies | Appointment platelet code is `PLT`; the donor's registered code for the same thing is `PL1`. |
| Truncated history | `hasFurtherDonations` signals it. Use `donationCredit` for the real total. |
| Undocumented codes | Donation `type` A/B/L/R, `sessionStatus` C/F, `correspondence` H, `newOrReturn` N. No published mapping. |
| Numbers as strings | `latitude`, `longitude`, `freeSlots`. |

### Session status combination

The app builds a four-character key and looks it up in a server-supplied table:

```js
buildSessionStatusCombo = ({sessionStatus, appointmentStatus, appointmentAvailability, bookingFlag}) =>
  sessionStatus + appointmentStatus + appointmentAvailability + bookingFlag
```

The lookup table (`sessionStatusCombos`) is **delivered at runtime through Firebase Remote Config**,
not by an API route, so this library exposes the raw key as `Session.status_combo` rather than
guessing at meanings.

That conclusion is worth recording in full, because it closes off the obvious next avenue:

- The literal `sessionStatusCombos` appears **exactly once** in `assets/index.android.bundle`, in the
  Hermes string table — the property access itself. No key/value table is embedded anywhere in the
  bundle, so it cannot be read out of the app.
- The bundle does contain `fetchAndActivate` and `remoteConfig`, which are the Firebase Remote Config
  JS SDK. The table is fetched from Firebase at startup with the app's own Firebase project
  credentials, which the API does not expose and this library cannot supply.
- Sixteen plausible API routes were probed and **all return 404**: `/api/config`,
  `/api/app/config`, `/api/app/config/{platform}/{version}`, `/api/app/configuration/{platform}/{version}`,
  `/api/configuration`, `/api/app/init`, `/api/app/bootstrap`, `/api/app/settings`, `/api/app/startup`,
  `/api/session-status-combos`, `/api/app/session-status-combos`, `/api/features/config`,
  `/api/features/app`, `/api/app-config`, `/api/config/app`, and `/api/features/sessionStatusCombos`.
- `/api/features` returns only the six booleans listed above, with no combo table under any parameter
  combination tried.

So decoding these codes needs either the Firebase Remote Config payload or enough observed sessions to
correlate codes against what the app displays. Until then the raw codes stay raw, deliberately: a
mapping derived from a single observation would be worse than none, because consumers build on it.

## Other services the app talks to

Not part of this library, but present in the bundle and worth knowing about:

- **Queue-it** (`libqueueit_app.so`, `libqueueit_library.so`) — a virtual waiting room in front of
  booking during high demand. This client does not implement it; if NHSBT enables queueing, booking
  calls may be redirected.

  **What a queued response looks like could not be observed.** Queueing was not active during any
  capture, and it only applies during genuine high demand — after a public appeal — so it cannot be
  produced on demand. The candidates, none confirmed: an HTTP 302 to a `queue-it.net` host, a 403 with
  a distinctive body, or a specific `errorCode` in the standard validation envelope. Anything named
  here would be a guess, and a wrong classifier is worse than none around booking.

  Read paths are almost certainly unaffected — NHSBT fronts the booking journey, not account reads,
  and this library's reads all worked during a period when nothing was queued. The exposure is the
  write paths: a queued response would currently surface as `GiveBloodBookingError`, i.e. "the API
  refused this booking" when the truth is "you are in a queue". That is misleading in exactly the case
  where a caller might retry. Mitigations already in place: writes are never retried on ambiguous
  failure, and `async_get_failover()` exposes NHSBT's own outage banner.
- **Azure Application Insights** — client telemetry.
- **Firebase** — analytics, Crashlytics, cloud messaging.
- **Google Maps Geocoding** — address lookup, with its own API key.

## Reproducing the analysis

```bash
brew install jadx                      # not strictly needed; logic is not in DEX
uv tool install hermes-dec

unzip -q "NHS+Give+Blood_4.9.1.xapk" -d xapk
unzip -q xapk/com.savant.mobile.nhs.nhsgiveblood.apk -d base
hbc-decompiler base/assets/index.android.bundle bundle.js

# The route table: one object literal holding every path.
grep -n "api/auth/v2/login" bundle.js

# The axios instance: base URL, standing headers, interceptors.
grep -n "baseURL" bundle.js | head
grep -n "addApiKeyInterceptor" bundle.js
```

> **If you capture live responses, sanitise them before they go anywhere near git.** Every account
> endpoint returns the donor's name, address, phone number, date of birth and donor ID. Use
> `scripts/sanitise_capture.py`, and let the `check_pii` pre-commit hook check your work.
