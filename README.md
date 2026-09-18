# Emporia Lite

Single-household Emporia power dashboard. Amps only (no watts), real per-circuit
current draw, true hourly max (not average), plus an all-time peak per circuit.
Serverless: 2 Lambda functions, 1 DynamoDB table, no EC2/ALB/NAT/S3.

See `docs/superpowers/specs/2026-08-20-emporia-lite-remodel-design.md` for the
full design rationale and `docs/superpowers/plans/2026-08-21-emporia-lite-implementation.md`
for the implementation plan (has the original code for every file, useful if
you want to see how a piece was originally built before later fixes touched it).

## How it works

- **`poller`** — an AWS Lambda that runs once an hour (via EventBridge). It
  logs into Emporia, pulls the last hour's per-second amp data for every
  circuit, and writes the true hourly max (not an average) to DynamoDB. It
  also updates two things: a "LATEST" row per circuit (last hour's peak,
  shown on page load) and an "ALL_TIME_MAX" row per circuit (never expires,
  only updates when a new reading beats the record).
- **`web`** — the other Lambda, reachable directly over HTTPS via a Lambda
  Function URL (no separate web server, load balancer, or API Gateway
  needed). Serves the dashboard page and two JSON routes:
  - `GET /api/history` — last-hour-peak + all-time-peak per circuit, plus
    30 days of hourly whole-home ("Main") history for the graph.
  - `GET /api/live` — an on-demand real-time reading, only called while the
    page's "Live reading" toggle is switched on (off by default, to avoid
    hammering Emporia's API).
- **DynamoDB** (`EmporiaReadings`) — one table holds everything: hourly
  history (auto-deleted after 30 days via DynamoDB TTL), the latest reading
  per circuit, and the all-time-max per circuit (no TTL, so it's the one
  place peaks survive past 30 days).
- **Whole-home total ("Main")** — Emporia's API won't report amps for its
  own combined circuit, so this app computes it itself by summing the two
  incoming service legs' per-second readings before taking the max (not
  summing each leg's individual max — see `emporia_client.py` for why that
  distinction matters).

Amps come straight from Emporia's own `AmpHours` API parameter — nothing in
this app derives amps from watts or a voltage assumption.

## Project structure

```
src/poller/handler.py     # hourly cron Lambda — see "How it works" above
src/web/handler.py        # HTTP-facing Lambda — page + /api/history + /api/live
src/web/page.py           # the entire dashboard page (HTML + CSS + JS) as one string
src/shared/emporia_client.py  # all Emporia API calls, including the Main-leg-summing logic
src/shared/dynamo.py       # all DynamoDB reads/writes
src/shared/secrets.py      # fetches Emporia login from AWS Secrets Manager
template.yaml              # AWS SAM template — defines every AWS resource this app uses
tests/                     # one test file per src/ file above
scripts/verify_amphours.py # standalone diagnostic script, not part of the deployed app
```

## Local development

```bash
pip install -r requirements-dev.txt
pytest
```

## Deploy

```bash
sam build
sam deploy --guided   # first time only; then just `sam deploy`
```

Requires a Secrets Manager secret (ARN passed as the `EmporiaSecretArn`
parameter) containing:

```json
{"EMPORIA_EMAIL": "...", "EMPORIA_PASSWORD": "..."}
```
