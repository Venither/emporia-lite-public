# Emporia Lite

A serverless dashboard for [Emporia Vue](https://www.emporiaenergy.com/) home energy
monitors, showing **amps** (real electrical current) per circuit instead of the
watts/kWh Emporia's own app defaults to.

I built this after finding Emporia's app answers "how much energy did I use"
well but not "how close is this circuit to tripping its breaker" — which is
the question that actually matters when you're staring at a breaker panel.
Amps pulled straight from Emporia's own API, true hourly peaks instead of
averages (a 20A spike averaged over an hour looks nothing like 20A), and an
all-time peak per circuit that survives past Emporia's own retention window.

This is a portfolio copy of a project I run for my own house — see
[What this repo is](#what-this-repo-is) below.

## What it looks like

- A table of every circuit, its last-hour peak amps, and its all-time peak amps
- A 30-day graph of whole-home peak amps per hour
- An optional "live" toggle for an on-demand real-time reading (off by
  default, so it doesn't hammer Emporia's API on every page load)

## Why amps, and why this exists

Emporia's own API won't report amps for its combined whole-home channel —
only per-leg. So whole-home ("Main") amps has to be computed by summing the
two split-phase legs' *per-second* readings before taking the max — summing
each leg's own max instead overstates peaks that never happened
simultaneously. That distinction (and a handful of other non-obvious things
learned by testing directly against a real Emporia account — pagination,
warm-Lambda client caching, pyemvue's retry loop colliding with the Lambda
timeout) is documented inline in the code and in `docs/`.

## Architecture

No EC2, load balancer, NAT gateway, or API Gateway — just:

```
EventBridge (hourly) ──▶ PollerFunction ──▶ DynamoDB ◀── WebFunction ◀── HTTPS (Function URL)
                          (Lambda)          (1 table)     (Lambda)
```

- **`poller`** — a Lambda on an hourly EventBridge schedule. Logs into
  Emporia, pulls the last hour's per-second amp data for every circuit, and
  writes the *true* hourly max (not an average) to DynamoDB. Also maintains
  a "LATEST" row per circuit (shown on page load) and an "ALL_TIME_MAX" row
  per circuit (never expires — the one place peaks survive past 30 days).
- **`web`** — a second Lambda, reachable directly over HTTPS via a Lambda
  Function URL (no web server or load balancer needed). Serves the
  dashboard page plus two JSON routes:
  - `GET /api/history` — last-hour-peak + all-time-peak per circuit, plus
    30 days of hourly whole-home history for the graph.
  - `GET /api/live` — an on-demand real-time reading, only called while the
    page's "Live reading" toggle is on.
- **DynamoDB** (`EmporiaReadings`) — a single table holds hourly history
  (auto-expired after 30 days via TTL), the latest reading per circuit, and
  the all-time-max per circuit (no TTL).

```
src/poller/handler.py         hourly cron Lambda
src/web/handler.py            HTTP-facing Lambda — page + /api/history + /api/live
src/web/page.py               the entire dashboard page (HTML + CSS + JS) as one string
src/shared/emporia_client.py  all Emporia API calls, including the Main-leg-summing logic
src/shared/dynamo.py          all DynamoDB reads/writes
src/shared/secrets.py         fetches Emporia login from AWS Secrets Manager
template.yaml                 AWS SAM template — every AWS resource this app uses
tests/                        one test file per src/ file above
scripts/verify_amphours.py    standalone diagnostic script, not part of the deployed app
```

`docs/superpowers/specs/2026-08-20-emporia-lite-remodel-design.md` has the
full design rationale, and `docs/superpowers/plans/2026-08-21-emporia-lite-implementation.md`
has the original implementation plan (useful if you want to see how a piece
was originally built, before later fixes touched it).

## Setup

### Prerequisites

- An AWS account. This deploys real (small, billed) resources — DynamoDB is
  pay-per-request and both Lambdas are well within the free tier for a
  single household, but it isn't free hosting.
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
  installed and configured (`aws configure`) with credentials that can
  create Lambda, DynamoDB, IAM, EventBridge, and Secrets Manager resources.
- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html) installed.
- Python 3.12.
- An Emporia Vue monitor already installed and set up in the Emporia app —
  this dashboard only *reads* from an existing Emporia account, it doesn't
  help with the physical install.

### 1. Clone and install dev dependencies (optional, for running tests)

```bash
git clone https://github.com/Venither/emporia-lite-public.git
cd emporia-lite-public
pip install -r requirements-dev.txt
pytest
```

### 2. Store your Emporia credentials in Secrets Manager

```bash
aws secretsmanager create-secret \
  --name emporia-lite/credentials \
  --secret-string '{"EMPORIA_EMAIL":"you@example.com","EMPORIA_PASSWORD":"your-emporia-password"}'
```

Note the `ARN` in the output — you'll pass it as a deploy parameter next.

### 3. Build and deploy

```bash
sam build
sam deploy --guided
```

`--guided` walks you through a stack name, AWS region, and the
`EmporiaSecretArn` parameter (paste the ARN from step 2) — and saves those
answers to `samconfig.toml` so future deploys are just `sam deploy`.

### 4. Get your dashboard URL

```bash
aws cloudformation describe-stacks \
  --stack-name <your-stack-name> \
  --query "Stacks[0].Outputs[?OutputKey=='WebFunctionUrl'].OutputValue" \
  --output text
```

(Also printed at the end of `sam deploy --guided`.)

### 5. Wait for the first data — or seed it immediately

The dashboard has no data until the poller runs at least once, and it's on
an hourly EventBridge schedule. To see data right away instead of waiting
up to an hour:

```bash
aws lambda invoke --function-name <your-stack-name>-PollerFunction-<suffix> /tmp/out.json
```

(Find the exact function name with `aws lambda list-functions --query "Functions[?contains(FunctionName, 'PollerFunction')].FunctionName"`.)

## What this repo is

This is a public copy of a project I built and actively run for my own
house. I split it from the private repo before open-sourcing it because the
private version's README linked my live dashboard, which has **no
authentication on its Function URL by design** (see the comment in
`template.yaml`) — fine for a link only I have, not fine to publish. The
code here is otherwise identical: same 26 commits, same design decisions,
same test suite. Deploy it against your own Emporia account and AWS account
to run your own instance.

## License

[MIT](LICENSE)
