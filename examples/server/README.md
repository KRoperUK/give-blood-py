# Dashboard server

A read-only web front end for this library: your upcoming appointments, and every
bookable session at the centres you use — preferred and previously donated at —
by month, week or day.

It never calls a write endpoint. There is no way to book, reschedule or cancel
from here; clicking a time copies a reference and opens the real booking site.

## Running it

Two processes, which is the setup for working on the interface — Vite serves the
UI with hot reload and proxies `/api` to the Python process:

```bash
# terminal 1 — the API, and the only process that talks to NHSBT
poetry run python examples/server/main.py --env-file ../.env

# terminal 2 — the UI on http://localhost:5173
npm --prefix examples/server/frontend install
npm --prefix examples/server/frontend run dev
```

Or build once and let the Python process serve everything on
<http://127.0.0.1:8080>:

```bash
npm --prefix examples/server/frontend run build
poetry run python examples/server/main.py --env-file ../.env
```

Credentials are resolved `--email`/`--password`, then the environment, then
`--env-file` (default `.env`). Prefer the environment or the file: a password on
the command line lands in your shell history.

If `npm run dev` reports `vite: command not found`, npm omitted dev dependencies
because `NODE_ENV=production` is set in that shell — Vite is one. Install them
explicitly with `npm install --include=dev`.

## Endpoints

| Endpoint | What it returns |
|---|---|
| `/api/summary` | donor, eligibility, next appointment, booking-system notices |
| `/api/appointments` | upcoming appointments |
| `/api/centres` | the preferred and previous centres availability covers |
| `/api/availability` | a per-centre summary with the next sessions |
| `/api/calendar?start=&end=` | every session in a range, grouped by day and flagged bookable |
| `/api/slots?session_id=&date=&start=&end=` | the times inside one session period |
| `/api/slots/batch` (POST) | the same for many periods at once |
| `/healthz` | liveness, without touching the API |

Add `?refresh=1` to any read to bypass the cache and wait for fresh data.

## Caching

Reads are cached per kind, because the underlying data changes at different
rates:

| Kind | Fresh for | Then |
|---|---|---|
| donor details and appointments | 3 × `--cache-ttl` | served stale while it refreshes, up to 12 × |
| a centre's sessions (the calendar) | `--cache-ttl` (five minutes) | served stale while it refreshes, up to 6 × |
| individual slot times | a fifth of that, at least 30s | never served stale |

Past its freshness window a value is still returned immediately and refreshed in
the background, so a page that is a few minutes old renders instantly. Slot times
are the exception: someone else can take one between two loads, so a stale one is
never shown — the read waits instead. The **Full refresh** button forces every
read, including the times.

`/api/calendar` and `/api/availability` report how old the reads behind them are
in `cache`, which the UI shows as "as of …".

## Querying the API by hand

```bash
curl -s localhost:8080/api/calendar | jq '.days[0].sessions[0]'
curl -s "localhost:8080/api/slots?session_id=XXXXX&date=2026-11-08&start=0830&end=1205" | jq
```

## Layout

```
main.py                 API, cache, and serving the built UI
frontend/
  vite.config.js        dev server on :5173, proxies /api to :8080
  src/
    App.jsx             state, toolbar, view switching
    views/              MonthView, WeekView, DayView
    components/         SessionCard + the times inside a period
    api.js              the read-only API client
    dates.js            calendar arithmetic
    format.js           locale-aware dates and times
```

Dates and times render in the browser's locale, falling back to `en-GB` — these
are UK venues, and "1st January 2001, 12:30pm" is what a UK reader expects. Add
`?locale=de-DE` to the URL to see another locale.
