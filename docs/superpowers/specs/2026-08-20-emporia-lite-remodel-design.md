# Emporia Lite — Remodel Design

**Date:** 2026-08-20
**Status:** Approved, pending implementation plan

## Background

`emporia-family-tracker` (the current single-household fork of the Emporia power
dashboard, running on an EC2 `t3.micro` at port 5002) has grown to 1000+ lines
covering an electricians directory, an EV charging calculator, a breaker
advisor, a changelog tab, a debug-mode frequency toggle, and a timezone
dropdown — far beyond its original purpose. This remodel starts a fresh,
minimal repo (`emporia-lite`) that does one thing: show real circuit-level
current draw and true (not averaged) peak draw, with almost no moving parts.

An AWS cost investigation during design turned up that the account's real
cost driver is an unrelated project ("Sierra Club Huts" — an idle ALB plus
two unassociated Elastic IPs in `us-east-2`, ~$25-30/mo) competing for the
account's shared EC2 free-tier hours, not the Emporia box itself. That's
being left alone — out of scope here — but it means the serverless rebuild
below is worth doing on its own merits (cost, simplicity), not as a fix for
a bill that was never Emporia's fault.

## Goals

- Show live per-circuit current draw and the true peak (max, not average)
  draw per circuit, per hour.
- Pull amp data directly from Emporia's own metering API — no watts, no
  voltage assumptions, no client-side unit conversion.
- A single, plain page: a live readings table and one 30-day history graph.
  No dashboards-of-dashboards, no animation, no unrelated tabs.
- Minimize AWS footprint and spend — nothing should run, or cost money,
  when nobody is looking at the page.

## Non-goals (explicitly cut from the current app)

- Electricians directory / Google Maps search
- EV charger calculator
- Breaker advisor (NEC 80% rule warnings) and its per-circuit
  breaker-size/voltage config UI
- Probe-rotation detection (auto-detecting the Emporia probe moving between
  panels)
- Changelog tab, timezone dropdown, debug-frequency toggle (the old one —
  see the new live/debug toggle below, which replaces it with something
  simpler)
- Sponsored electrician listings
- Any authentication — this stays a fully open, unauthenticated page, same
  as the current family tracker

## Architecture

Two Lambda functions, one DynamoDB table, one EventBridge schedule. No EC2,
no ALB, no NAT gateway, no S3, no API Gateway (Lambda Function URLs supply
HTTPS directly).

```
EventBridge (hourly) ──> poller Lambda ──> DynamoDB (EmporiaReadings)
                                                  ^
                                                  |
Browser <──> web Lambda (Function URL) ──────────┘
   |            (GET /, GET /api/history)
   └──(toggle on)──> web Lambda (GET /api/live) ──> Emporia API (pass-through, no DB write)
```

- **`poller`** — triggered hourly by an EventBridge scheduled rule. Logs
  into Emporia (credentials from Secrets Manager, same as today), enumerates
  devices/channels, and for each channel calls `get_chart_usage()` for the
  full previous hour at 1-second resolution. This is the same trick the
  current app already uses to get a *true* max instead of a max sampled at
  the polling interval: Emporia retains per-second history, so fetching the
  whole hour after the fact and taking the max of that series is accurate
  regardless of how often the poller itself runs. Writes the per-circuit
  hourly max to DynamoDB and updates each circuit's `LATEST` reading item.

- **`web`** — one Lambda behind a Function URL, handling three routes:
  - `GET /` — returns the single HTML page (CSS/JS inline, no build step,
    no S3 bucket to manage).
  - `GET /api/history` — returns the 30-day whole-home amp history plus the
    latest per-circuit readings, for the live table.
  - `GET /api/live` — a live pass-through call to Emporia's API, used only
    while the live/debug toggle (see below) is switched on.

This is the entire AWS surface: 2 Lambda functions, 1 DynamoDB table, 1
EventBridge rule, plus the Secrets Manager secret already in use for Emporia
credentials. Nothing runs continuously; nothing is billed for idle time.

## Data source: amps, not watts

pyemvue's `Unit` enum includes `AMPHOURS = "AmpHours"`, and Emporia's real
backend endpoints (`getChartUsage`, `getDeviceListUsages`) accept
`energyUnit=AmpHours` directly, the same way the current app passes
`energyUnit=KilowattHours`. This means instantaneous amps can be read
straight from Emporia's own metering, with no derivation from watts and no
voltage assumptions:

```python
def amphours_to_amps(amp_hours: float, scale_seconds: int) -> float:
    """Same time-scaling the current app uses for kWh→watts, applied to
    amp-hours instead: an amp-hours reading for a `scale_seconds` bucket,
    converted to instantaneous amps."""
    if not amp_hours or amp_hours < 0:
        return 0.0
    return amp_hours * (3600 / scale_seconds)
```

Both `poller` (hourly, `Scale.SECOND` over the full previous hour) and the
`/api/live` route (on-demand, single `Scale.SECOND` reading) call the
Emporia API with `unit=Unit.AMPHOURS.value` and run readings through this
conversion. Watts do not appear anywhere in this app — not in the data
model, not in the API responses, not in the UI.

## Data model (DynamoDB, single table: `EmporiaReadings`)

| Attribute | Example | Notes |
|---|---|---|
| `PK` (circuit_id) | `"482910:3"` | `{device_gid}:{channel_num}`, same scheme as today |
| `SK` | `"2026-08-20T14:00:00Z"` or `"LATEST"` | Hourly rows use the hour timestamp; one `LATEST` item per circuit holds the current reading |
| `name` | `"Kitchen"` | Circuit display name from Emporia |
| `max_amps` | `9.4` | True max for that hour (hourly rows) or current reading (`LATEST`) |
| `ttl` | epoch seconds, now + 30 days | DynamoDB TTL auto-deletes hourly rows after 30 days — no cleanup job |

The whole-home total is not separately computed — Emporia already exposes
it as its own channel (the `Main`/`Balance` circuit, same as the current
app), so the 30-day graph is simply that circuit's hourly-row series.

## Live/debug toggle

Off by default. Flipping it on makes the browser call `GET /api/live`
every few seconds for a real-time amps reading pulled directly from
Emporia — no DynamoDB writes, no change to the poller's schedule, zero
added cost while off. `/api/live` is unauthenticated (matching the app's
no-login design), so it's technically abusable by an outside caller
hitting the URL directly and driving up Lambda/Emporia-API calls. Given the
page is a low-value, unlisted target with no sensitive data, this is an
accepted risk rather than something worth adding auth to solve.

## Frontend

Single HTML page, inline CSS/JS, no framework, no build step. Two things
on the page:

1. **Live table** — one row per circuit, current amps reading, refreshed
   from `LATEST` items every page load (and every few seconds if the
   live/debug toggle is on).
2. **History graph** — one line, whole-home amps, 30 days, hourly
   resolution (720 points). Chart.js, same as today. Zoom/pan kept since
   it's functional (needed to read 720 points legibly), not decorative.

No other tabs, no settings panel, no export buttons, no theming.

## Deployment

AWS SAM (`template.yaml` + `sam build && sam deploy`). Declaratively
defines both Lambdas, the DynamoDB table (with TTL enabled), the
EventBridge schedule, and the Function URL — one file, one deploy command,
no Terraform, no manual `aws` CLI resource creation. Updates going forward
are `sam build && sam deploy` instead of the old `scp` + `systemctl
restart` flow.

## Cost estimate

At this traffic level (a handful of circuits, hourly polling, occasional
family page views): DynamoDB and Lambda both fall well within their
perpetual free tiers (1M free Lambda requests/month, 25GB + on-demand
pricing fractions of a cent for DynamoDB at this volume). Realistic
expected cost: **$0-1/month**, versus the current EC2 box's exposure to
the account's shared free-tier-hours contention described in Background.

## Error handling

- `poller` — if a channel's `get_chart_usage()` call fails for a given
  hour, log and skip that channel for that hour (matches current
  behavior); does not block other channels or the next hourly run.
- `web` — if Emporia login fails on `/api/live`, return a clear error to
  the frontend rather than a raw stack trace; the live table falls back to
  showing the last known `LATEST` reading from DynamoDB.
- No retry queues, no dead-letter handling — at this scale a missed hourly
  poll just means one gap in the 30-day graph, which is an acceptable
  trade-off for staying simple.

## Testing

- Unit tests for `amphours_to_amps()` and the hourly-max reconstruction
  logic — pure functions, testable without touching Emporia or AWS.
- Manual smoke test against the real Emporia account for `poller` and both
  `web` routes before first deploy, since `AMPHOURS` as a live unit
  parameter hasn't been exercised against Emporia's backend in this
  codebase before (only inferred from the pyemvue enum and URL template —
  worth confirming the values that come back look like real amps before
  relying on them).

## Open risks / things to confirm during implementation

- **`AMPHOURS` unit not yet verified against live Emporia data.** The
  enum and endpoint parameter exist in pyemvue, but no request has
  actually been made with `energyUnit=AmpHours` yet. First implementation
  step should be a throwaway script confirming the returned values convert
  to plausible amp readings (e.g., sanity-check against a known appliance
  load) before building the rest of the pipeline on top of it.
- **`/api/live` is unauthenticated and publicly reachable** if the URL
  leaks — accepted risk per above, not solved here.
