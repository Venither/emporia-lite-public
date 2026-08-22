# Emporia Lite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and deploy a serverless, amps-only Emporia power dashboard: live per-circuit current draw, true hourly max (not average), and a 30-day whole-home history graph.

**Architecture:** Two AWS Lambda functions (`poller`, hourly cron via EventBridge; `web`, a Function URL serving the page + two API routes) sharing one DynamoDB table. No EC2, ALB, NAT gateway, S3, or API Gateway. Credentials come from a dedicated Secrets Manager secret. Amps are read directly from Emporia's `AmpHours` unit — never derived from watts.

**Tech Stack:** Python 3.12, `pyemvue` 0.18.9, `boto3`, AWS SAM for packaging/deploy, DynamoDB (pay-per-request), pytest + `moto` for tests, Chart.js (CDN) for the frontend graph.

## Global Constraints

- Amps only, everywhere — no watts anywhere in the data model, API responses, or UI. Amps come directly from Emporia's `Unit.AMPHOURS` API parameter, never computed from watts/voltage.
- DynamoDB table name is exactly `EmporiaReadings` (single table, `PK`/`SK` composite key).
- Lambda runtime is `python3.12`.
- No new authentication anywhere — the app stays fully open, matching the current family tracker.
- No S3, no ALB, no NAT gateway, no API Gateway — Lambda Function URLs supply HTTPS directly.
- Emporia credentials come from a Secrets Manager secret (JSON with keys `EMPORIA_EMAIL` / `EMPORIA_PASSWORD`), read via `EMPORIA_SECRET_ARN` env var — never hardcoded, never read from a local `.env` inside Lambda code.
- History retention is 30 days via DynamoDB TTL — no manual cleanup job.
- Full spec: `docs/superpowers/specs/2026-08-20-emporia-lite-remodel-design.md`.

---

## Pre-verified facts (do not re-derive — these were confirmed live against the real Emporia account during design)

- `pyemvue`'s `Unit.AMPHOURS` (`"AmpHours"`) works for individual circuits and for the two mains legs (`Mains_A`, `Mains_B`), both via `get_chart_usage` (history) and `get_device_list_usage` (live).
- Emporia's API **rejects** `AmpHours` for its own combined multi-leg channel (channel_num containing a comma, e.g. `"1,2,3"`) with `{"message":"AmpHours cannot be used with the combined mains channel"}`. This channel must be excluded from polling entirely.
- `vue.get_devices()` returns `VueDevice` objects whose `.channels` is a **list** of `VueDeviceChannel` — not a dict. (Only the usage-response shape from `get_device_list_usage`, `VueUsageDevice.channels`, is a dict.) Get this wrong and channel enumeration silently breaks.
- `Mains_A` / `Mains_B` are not enumerable via `get_devices()` — they're addressed by constructing a `VueDeviceChannel(gid=device_gid, name=leg, channelNum=leg)` directly and passing it to `get_chart_usage`/live calls.
- Live-summing `Mains_A + Mains_B` reproduces the same value Emporia's own combined channel reports live (cross-validated: both read 4.90A in the same instant during verification) — confirms the sum is the correct whole-home figure, not just a plausible guess.

---

### Task 1: Repo scaffolding + amps conversion math

**Files:**
- Create: `requirements.txt`
- Create: `requirements-dev.txt`
- Create: `pytest.ini`
- Create: `.gitignore`
- Create: `src/shared/__init__.py`
- Create: `src/shared/emporia_client.py`
- Test: `tests/test_emporia_client.py`

**Interfaces:**
- Produces: `emporia_client.SCALE_SECONDS: int`, `emporia_client.MAINS_LEGS: tuple[str, str]`, `emporia_client.WHOLE_HOME_NAME: str`, `emporia_client.amphours_to_amps(amp_hours, scale_seconds=SCALE_SECONDS) -> float`, `emporia_client.max_amps_from_series(amp_hours_series, scale_seconds=SCALE_SECONDS) -> float`

- [ ] **Step 1: Create the directory structure and dependency files**

```bash
mkdir -p src/shared src/poller src/web tests scripts
touch src/shared/__init__.py src/poller/__init__.py src/web/__init__.py
```

`requirements.txt`:
```
pyemvue==0.18.9
```

`requirements-dev.txt`:
```
-r requirements.txt
boto3==1.43.37
pytest==9.1.1
moto[dynamodb]==5.2.2
python-dotenv
```

`pytest.ini`:
```ini
[pytest]
pythonpath = src
```

`.gitignore`:
```
__pycache__/
*.pyc
.pytest_cache/
.aws-sam/
samconfig.toml
.env
```

- [ ] **Step 2: Install dev dependencies**

```bash
pip install -r requirements-dev.txt
```

- [ ] **Step 3: Write the failing tests for the amps conversion math**

`tests/test_emporia_client.py`:
```python
from unittest.mock import MagicMock
from shared import emporia_client


def test_amphours_to_amps_converts_one_second_bucket():
    # 1 amp for 1 second = 1/3600 amp-hours
    assert emporia_client.amphours_to_amps(1 / 3600, scale_seconds=1) == 1.0


def test_amphours_to_amps_zero_for_none_or_negative():
    assert emporia_client.amphours_to_amps(None) == 0.0
    assert emporia_client.amphours_to_amps(-1) == 0.0


def test_max_amps_from_series_picks_true_peak_not_average():
    # ten seconds of 1A, one second of 20A spike, ten seconds of 1A
    series = [1 / 3600] * 10 + [20 / 3600] + [1 / 3600] * 10
    result = emporia_client.max_amps_from_series(series, scale_seconds=1)
    assert abs(result - 20.0) < 0.001


def test_max_amps_from_series_ignores_none_entries():
    series = [None, 1 / 3600, None]
    result = emporia_client.max_amps_from_series(series, scale_seconds=1)
    assert abs(result - 1.0) < 0.001


def test_max_amps_from_series_empty_returns_zero():
    assert emporia_client.max_amps_from_series([]) == 0.0
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest tests/test_emporia_client.py -v`
Expected: FAIL with `ModuleNotFoundError` or `AttributeError` (module/functions don't exist yet)

- [ ] **Step 5: Implement the amps conversion math**

`src/shared/emporia_client.py`:
```python
import pyemvue
from pyemvue.device import VueDeviceChannel
from pyemvue.enums import Scale, Unit

SCALE_SECONDS = 1  # matches Scale.SECOND
MAINS_LEGS = ("Mains_A", "Mains_B")
WHOLE_HOME_NAME = "Main"


def amphours_to_amps(amp_hours, scale_seconds=SCALE_SECONDS):
    if not amp_hours or amp_hours < 0:
        return 0.0
    return amp_hours * (3600 / scale_seconds)


def max_amps_from_series(amp_hours_series, scale_seconds=SCALE_SECONDS):
    amps = [amphours_to_amps(v, scale_seconds) for v in amp_hours_series if v is not None]
    return max(amps) if amps else 0.0
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_emporia_client.py -v`
Expected: 5 tests PASS

- [ ] **Step 7: Commit**

```bash
git add requirements.txt requirements-dev.txt pytest.ini .gitignore src/shared/__init__.py src/shared/emporia_client.py tests/test_emporia_client.py
git commit -m "feat: scaffold project and add amps conversion math"
```

---

### Task 2: Emporia API wrappers (channels, mains legs, Main combining)

**Files:**
- Modify: `src/shared/emporia_client.py`
- Modify: `tests/test_emporia_client.py`

**Interfaces:**
- Consumes: `emporia_client.amphours_to_amps`, `emporia_client.max_amps_from_series`, `emporia_client.MAINS_LEGS`, `emporia_client.WHOLE_HOME_NAME` (from Task 1)
- Produces: `emporia_client.login(email, password) -> pyemvue.PyEmVue`, `emporia_client.fetch_channels(vue) -> list[dict]` (each dict: `circuit_id, name, device_gid, channel_num, channel_obj`), `emporia_client.mains_leg_channels(device_gid) -> list[dict]` (same shape), `emporia_client.fetch_hour_max_amps(vue, channel_obj, hour_start, hour_end) -> float`, `emporia_client.fetch_main_hour_max_amps(vue, device_gid, hour_start, hour_end) -> float`, `emporia_client.fetch_live_amps(vue, device_gids) -> dict[str, float]`, `emporia_client.compute_main_from_readings(readings, device_gid) -> float | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_emporia_client.py`:
```python
def test_fetch_channels_iterates_channel_list_not_dict():
    fake_channel = MagicMock()
    fake_channel.channel_num = "1"
    fake_channel.name = "Kitchen"

    fake_device = MagicMock()
    fake_device.device_gid = 12345
    fake_device.channels = [fake_channel]  # list, matching real VueDevice.channels

    fake_vue = MagicMock()
    fake_vue.get_devices.return_value = [fake_device]

    result = emporia_client.fetch_channels(fake_vue)

    assert result == [{
        "circuit_id": "12345:1",
        "name": "Kitchen",
        "device_gid": 12345,
        "channel_num": "1",
        "channel_obj": fake_channel,
    }]


def test_fetch_channels_excludes_combined_multi_leg_channel():
    combined = MagicMock()
    combined.channel_num = "1,2,3"
    combined.name = "Circuit 1,2,3"

    real = MagicMock()
    real.channel_num = "1"
    real.name = "Kitchen"

    fake_device = MagicMock()
    fake_device.device_gid = 12345
    fake_device.channels = [combined, real]

    fake_vue = MagicMock()
    fake_vue.get_devices.return_value = [fake_device]

    result = emporia_client.fetch_channels(fake_vue)

    assert [c["circuit_id"] for c in result] == ["12345:1"]


def test_mains_leg_channels_returns_both_legs():
    result = emporia_client.mains_leg_channels(12345)
    assert [c["circuit_id"] for c in result] == ["12345:Mains_A", "12345:Mains_B"]
    assert [c["name"] for c in result] == ["Mains_A", "Mains_B"]


def test_fetch_main_hour_max_amps_sums_legs_before_taking_max_not_after():
    # Leg A peaks at index 0, leg B peaks at index 1 — they never peak
    # together, so the true combined max is the max of the SUMMED series
    # (5+1=6 or 1+6=7), not max(A)+max(B) (5+6=11).
    leg_a_series = [5 / 3600, 1 / 3600]
    leg_b_series = [1 / 3600, 6 / 3600]

    fake_vue = MagicMock()
    fake_vue.get_chart_usage.side_effect = [
        (leg_a_series, "start"),
        (leg_b_series, "start"),
    ]

    import datetime
    start = datetime.datetime(2026, 8, 20, 14, 0)
    end = datetime.datetime(2026, 8, 20, 15, 0)

    result = emporia_client.fetch_main_hour_max_amps(fake_vue, 12345, start, end)

    assert abs(result - 7.0) < 0.001  # max(5+1, 1+6) = 7, not 5+6=11


def test_fetch_main_hour_max_amps_returns_zero_when_a_leg_is_empty():
    fake_vue = MagicMock()
    fake_vue.get_chart_usage.side_effect = [([1 / 3600], "start"), ([], "start")]
    import datetime
    result = emporia_client.fetch_main_hour_max_amps(
        fake_vue, 12345, datetime.datetime(2026, 8, 20, 14, 0), datetime.datetime(2026, 8, 20, 15, 0)
    )
    assert result == 0.0


def test_compute_main_from_readings_sums_both_legs():
    readings = {"1:Mains_A": 2.3, "1:Mains_B": 1.4, "1:Kitchen": 9.4}
    assert emporia_client.compute_main_from_readings(readings, 1) == 3.7


def test_compute_main_from_readings_none_when_leg_missing():
    readings = {"1:Mains_A": 2.3}
    assert emporia_client.compute_main_from_readings(readings, 1) is None


def test_fetch_hour_max_amps_uses_amphours_unit():
    fake_vue = MagicMock()
    fake_vue.get_chart_usage.return_value = ([1 / 3600, 5 / 3600], "2026-08-20T14:00:00Z")
    fake_channel = MagicMock()

    import datetime
    start = datetime.datetime(2026, 8, 20, 14, 0)
    end = datetime.datetime(2026, 8, 20, 15, 0)

    result = emporia_client.fetch_hour_max_amps(fake_vue, fake_channel, start, end)

    assert abs(result - 5.0) < 0.001
    _, kwargs = fake_vue.get_chart_usage.call_args
    assert kwargs["unit"] == "AmpHours"


def test_fetch_live_amps_uses_amphours_unit():
    fake_channel_usage = MagicMock()
    fake_channel_usage.usage = 2 / 3600
    fake_channel_usage.channel_num = "1"

    fake_device = MagicMock()
    fake_device.channels = {"1": fake_channel_usage}

    fake_vue = MagicMock()
    fake_vue.get_device_list_usage.return_value = {42: fake_device}

    result = emporia_client.fetch_live_amps(fake_vue, [42])

    assert result == {"42:1": 2.0}
    args, kwargs = fake_vue.get_device_list_usage.call_args
    assert args[2] == "AmpHours" or kwargs.get("unit") == "AmpHours" or args[-1] == "AmpHours"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_emporia_client.py -v`
Expected: FAIL with `AttributeError` (functions don't exist yet)

- [ ] **Step 3: Implement the wrappers**

Append to `src/shared/emporia_client.py`:
```python
def login(email, password):
    vue = pyemvue.PyEmVue()
    vue.login(username=email, password=password)
    return vue


def fetch_channels(vue):
    """vue.get_devices() returns VueDevice objects whose .channels is a LIST of
    VueDeviceChannel (unlike the usage-dict responses from get_device_list_usage,
    where .channels is a dict) — see pyemvue/device.py VueDevice.channels.

    Excludes Emporia's own combined multi-leg channel (channel_num containing
    a comma, e.g. "1,2,3") — its API rejects AmpHours for that channel, and
    the whole-home total is computed separately from the two mains legs
    instead (see fetch_main_hour_max_amps / compute_main_from_readings)."""
    devices = vue.get_devices()
    channels = []
    for device in devices:
        if device is None or not device.channels:
            continue
        for channel in device.channels:
            if channel is None:
                continue
            ch_num = channel.channel_num
            if "," in ch_num:
                continue
            circuit_id = f"{device.device_gid}:{ch_num}"
            name = channel.name or f"Circuit {ch_num}"
            channels.append({
                "circuit_id": circuit_id,
                "name": name,
                "device_gid": device.device_gid,
                "channel_num": ch_num,
                "channel_obj": channel,
            })
    return channels


def mains_leg_channels(device_gid):
    """The two incoming split-phase service legs. Not enumerable via
    get_devices() — Emporia treats them as special channels, addressed
    directly by name. Individually queryable for amps; the combined mains
    channel is not (Emporia's API rejects AmpHours for it outright)."""
    return [
        {
            "circuit_id": f"{device_gid}:{leg}",
            "name": leg,
            "device_gid": device_gid,
            "channel_num": leg,
            "channel_obj": VueDeviceChannel(gid=device_gid, name=leg, channelNum=leg),
        }
        for leg in MAINS_LEGS
    ]


def fetch_hour_max_amps(vue, channel_obj, hour_start, hour_end):
    usage_list, _ = vue.get_chart_usage(
        channel_obj,
        start=hour_start,
        end=hour_end,
        scale=Scale.SECOND.value,
        unit=Unit.AMPHOURS.value,
    )
    return max_amps_from_series(usage_list or [])


def fetch_main_hour_max_amps(vue, device_gid, hour_start, hour_end):
    """True hourly max for the combined whole-home ('Main') reading.

    Sums the two mains legs' per-second amp-hours readings together
    index-by-index (NOT each leg's individual max — the legs don't
    necessarily peak at the same moment, so max(A)+max(B) would overstate
    a peak that never actually happened simultaneously) and returns the max
    instantaneous amps of that combined series. See design doc for the 240V
    double-counting caveat inherent to summing split-phase legs."""
    leg_channels = mains_leg_channels(device_gid)
    leg_series = []
    for leg in leg_channels:
        usage_list, _ = vue.get_chart_usage(
            leg["channel_obj"],
            start=hour_start,
            end=hour_end,
            scale=Scale.SECOND.value,
            unit=Unit.AMPHOURS.value,
        )
        leg_series.append(usage_list or [])

    if len(leg_series) != 2 or not leg_series[0] or not leg_series[1]:
        return 0.0

    combined = [(a or 0) + (b or 0) for a, b in zip(*leg_series)]
    return max_amps_from_series(combined)


def fetch_live_amps(vue, device_gids):
    usage_dict = vue.get_device_list_usage(
        device_gids, None, Scale.SECOND.value, Unit.AMPHOURS.value
    )
    readings = {}
    for gid, device in usage_dict.items():
        if device is None:
            continue
        for ch in (device.channels or {}).values():
            if ch is None or ch.usage is None:
                continue
            circuit_id = f"{gid}:{ch.channel_num}"
            readings[circuit_id] = round(amphours_to_amps(ch.usage), 1)
    return readings


def compute_main_from_readings(readings, device_gid):
    """Given a {circuit_id: amps} dict as returned by fetch_live_amps (which
    already includes Mains_A/Mains_B — get_device_list_usage returns them
    even though get_devices() doesn't enumerate them), sum the two legs into
    a single whole-home reading. Returns None if either leg is missing."""
    a = readings.get(f"{device_gid}:{MAINS_LEGS[0]}")
    b = readings.get(f"{device_gid}:{MAINS_LEGS[1]}")
    if a is None or b is None:
        return None
    return round(a + b, 1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_emporia_client.py -v`
Expected: 14 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/shared/emporia_client.py tests/test_emporia_client.py
git commit -m "feat: add Emporia channel enumeration and Main-combining logic"
```

---

### Task 3: Secrets Manager credential fetching

**Files:**
- Create: `src/shared/secrets.py`
- Create: `tests/test_secrets.py`

**Interfaces:**
- Produces: `secrets.get_emporia_credentials() -> tuple[str, str]` (reads `EMPORIA_SECRET_ARN` env var, returns `(email, password)`)

- [ ] **Step 1: Write the failing tests**

`tests/test_secrets.py`:
```python
import os, json
from unittest.mock import patch, MagicMock
from shared import secrets


def test_get_emporia_credentials_fetches_and_parses_secret():
    secrets._cache.clear()
    fake_client = MagicMock()
    fake_client.get_secret_value.return_value = {
        "SecretString": json.dumps({"EMPORIA_EMAIL": "a@b.com", "EMPORIA_PASSWORD": "hunter2"})
    }
    with patch.dict(os.environ, {"EMPORIA_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:1:secret:x"}), \
         patch("shared.secrets.boto3.client", return_value=fake_client):
        email, password = secrets.get_emporia_credentials()

    assert (email, password) == ("a@b.com", "hunter2")
    fake_client.get_secret_value.assert_called_once_with(
        SecretId="arn:aws:secretsmanager:us-east-1:1:secret:x"
    )


def test_get_emporia_credentials_caches_after_first_call():
    secrets._cache.clear()
    fake_client = MagicMock()
    fake_client.get_secret_value.return_value = {
        "SecretString": json.dumps({"EMPORIA_EMAIL": "a@b.com", "EMPORIA_PASSWORD": "hunter2"})
    }
    with patch.dict(os.environ, {"EMPORIA_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:1:secret:x"}), \
         patch("shared.secrets.boto3.client", return_value=fake_client):
        secrets.get_emporia_credentials()
        secrets.get_emporia_credentials()

    assert fake_client.get_secret_value.call_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_secrets.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`src/shared/secrets.py`:
```python
import json
import os

import boto3

_cache = {}


def get_emporia_credentials():
    """Fetch EMPORIA_EMAIL/EMPORIA_PASSWORD from the Secrets Manager secret
    named by the EMPORIA_SECRET_ARN env var. Cached at module scope so a warm
    Lambda execution environment only pays for one Secrets Manager call."""
    if "email" in _cache and "password" in _cache:
        return _cache["email"], _cache["password"]

    secret_arn = os.environ["EMPORIA_SECRET_ARN"]
    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=secret_arn)
    secret = json.loads(response["SecretString"])

    _cache["email"] = secret["EMPORIA_EMAIL"]
    _cache["password"] = secret["EMPORIA_PASSWORD"]
    return _cache["email"], _cache["password"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_secrets.py -v`
Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/shared/secrets.py tests/test_secrets.py
git commit -m "feat: fetch Emporia credentials from Secrets Manager"
```

---

### Task 4: DynamoDB access layer

**Files:**
- Create: `src/shared/dynamo.py`
- Create: `tests/test_dynamo.py`

**Interfaces:**
- Produces: `dynamo.get_table(table_name) -> Table`, `dynamo.put_hourly_max(table, circuit_id, hour, name, max_amps) -> None`, `dynamo.put_latest_reading(table, circuit_id, name, amps) -> None`, `dynamo.get_latest_readings(table) -> list[dict]`, `dynamo.get_history(table, circuit_id, since_iso) -> list[dict]`

- [ ] **Step 1: Write the failing tests**

`tests/test_dynamo.py`:
```python
import boto3
from moto import mock_aws
from shared import dynamo

TABLE_NAME = "EmporiaReadings"


def _make_table():
    client = boto3.client("dynamodb", region_name="us-east-1")
    client.create_table(
        TableName=TABLE_NAME,
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    return dynamo.get_table(TABLE_NAME)


@mock_aws
def test_put_and_get_latest_readings():
    table = _make_table()
    dynamo.put_latest_reading(table, "1:1", "Kitchen", 9.4)
    dynamo.put_latest_reading(table, "1:2", "Garage", 3.1)
    # an hourly row for the same circuits should NOT show up as a "latest" reading
    dynamo.put_hourly_max(table, "1:1", "2026-08-20T14:00:00Z", "Kitchen", 12.0)

    latest = dynamo.get_latest_readings(table)
    by_circuit = {item["PK"]: item for item in latest}

    assert set(by_circuit) == {"1:1", "1:2"}
    assert float(by_circuit["1:1"]["amps"]) == 9.4
    assert float(by_circuit["1:2"]["amps"]) == 3.1


@mock_aws
def test_get_history_returns_only_matching_circuit_within_window():
    table = _make_table()
    dynamo.put_hourly_max(table, "1:1", "2026-07-01T00:00:00Z", "Main", 10.0)  # outside 30-day window
    dynamo.put_hourly_max(table, "1:1", "2026-08-19T14:00:00Z", "Main", 11.0)
    dynamo.put_hourly_max(table, "1:1", "2026-08-20T14:00:00Z", "Main", 12.5)
    dynamo.put_hourly_max(table, "1:2", "2026-08-20T14:00:00Z", "Garage", 3.0)  # different circuit
    dynamo.put_latest_reading(table, "1:1", "Main", 12.5)  # must not appear in history

    history = dynamo.get_history(table, "1:1", "2026-08-01T00:00:00Z")

    assert [item["SK"] for item in history] == [
        "2026-08-19T14:00:00Z",
        "2026-08-20T14:00:00Z",
    ]
    assert [float(item["max_amps"]) for item in history] == [11.0, 12.5]


@mock_aws
def test_ttl_attribute_set_on_hourly_rows():
    table = _make_table()
    dynamo.put_hourly_max(table, "1:1", "2026-08-20T14:00:00Z", "Main", 5.0)
    item = table.get_item(Key={"PK": "1:1", "SK": "2026-08-20T14:00:00Z"})["Item"]
    assert "ttl" in item
    assert item["ttl"] > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dynamo.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`src/shared/dynamo.py`:
```python
import time
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key

HISTORY_TTL_SECONDS = 30 * 24 * 3600  # 30 days
_END_OF_TIME_SK = "9999-12-31T23:59:59Z"


def get_table(table_name):
    return boto3.resource("dynamodb").Table(table_name)


def put_hourly_max(table, circuit_id, hour, name, max_amps):
    table.put_item(Item={
        "PK": circuit_id,
        "SK": hour,
        "name": name,
        "max_amps": Decimal(str(round(max_amps, 2))),
        "ttl": int(time.time()) + HISTORY_TTL_SECONDS,
    })


def put_latest_reading(table, circuit_id, name, amps):
    table.put_item(Item={
        "PK": circuit_id,
        "SK": "LATEST",
        "name": name,
        "amps": Decimal(str(round(amps, 2))),
        "updated_at": int(time.time()),
    })


def get_latest_readings(table):
    """Full-table scan filtered to SK == 'LATEST'. Fine at this scale — a
    handful of circuits, never more than a few dozen items match."""
    response = table.scan(FilterExpression=Attr("SK").eq("LATEST"))
    return response.get("Items", [])


def get_history(table, circuit_id, since_iso):
    """All hourly rows for one circuit from since_iso onward. The upper
    bound excludes the 'LATEST' item because the string 'LATEST' sorts
    after any ISO date string in DynamoDB's byte-order comparison."""
    response = table.query(
        KeyConditionExpression=Key("PK").eq(circuit_id)
        & Key("SK").between(since_iso, _END_OF_TIME_SK)
    )
    return response.get("Items", [])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dynamo.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/shared/dynamo.py tests/test_dynamo.py
git commit -m "feat: add DynamoDB access layer for readings table"
```

---

### Task 5: Poller Lambda

**Files:**
- Create: `src/poller/__init__.py` (already created in Task 1 — verify it's empty and present)
- Create: `src/poller/handler.py`
- Create: `tests/test_poller_handler.py`

**Interfaces:**
- Consumes: `emporia_client.login`, `emporia_client.fetch_channels`, `emporia_client.fetch_hour_max_amps`, `emporia_client.fetch_main_hour_max_amps`, `emporia_client.WHOLE_HOME_NAME`, `dynamo.get_table`, `dynamo.put_hourly_max`, `dynamo.put_latest_reading`, `secrets.get_emporia_credentials` (all from Tasks 1-4)
- Produces: `handler.previous_hour_bounds(now=None) -> (datetime, datetime)`, `handler.hour_str(hour_start) -> str`, `handler.poll_and_store(vue, table, channels, device_gid, hour_start, hour_end) -> int`, `handler.lambda_handler(event, context) -> dict`

- [ ] **Step 1: Write the failing tests**

`tests/test_poller_handler.py`:
```python
import datetime
from unittest.mock import MagicMock, patch
from poller import handler


def test_previous_hour_bounds_truncates_to_top_of_hour():
    now = datetime.datetime(2026, 8, 20, 14, 37, 12)
    start, end = handler.previous_hour_bounds(now)
    assert start == datetime.datetime(2026, 8, 20, 13, 0, 0)
    assert end == datetime.datetime(2026, 8, 20, 14, 0, 0)


def test_hour_str_formats_as_iso_hour():
    start = datetime.datetime(2026, 8, 20, 13, 0, 0)
    assert handler.hour_str(start) == "2026-08-20T13:00:00Z"


def test_poll_and_store_writes_hourly_max_and_latest_for_each_channel_and_main():
    channels = [
        {"circuit_id": "1:1", "name": "Kitchen", "channel_obj": MagicMock()},
        {"circuit_id": "1:2", "name": "Garage", "channel_obj": MagicMock()},
    ]
    fake_vue = MagicMock()
    fake_table = MagicMock()
    hour_start = datetime.datetime(2026, 8, 20, 13, 0, 0)
    hour_end = datetime.datetime(2026, 8, 20, 14, 0, 0)

    with patch("poller.handler.emporia_client.fetch_hour_max_amps", side_effect=[9.4, 3.1]), \
         patch("poller.handler.emporia_client.fetch_main_hour_max_amps", return_value=42.0), \
         patch("poller.handler.dynamo.put_hourly_max") as mock_put_hourly, \
         patch("poller.handler.dynamo.put_latest_reading") as mock_put_latest:
        stored = handler.poll_and_store(fake_vue, fake_table, channels, 1, hour_start, hour_end)

    assert stored == 3
    assert mock_put_hourly.call_count == 3
    mock_put_hourly.assert_any_call(fake_table, "1:1", "2026-08-20T13:00:00Z", "Kitchen", 9.4)
    mock_put_hourly.assert_any_call(fake_table, "1:2", "2026-08-20T13:00:00Z", "Garage", 3.1)
    mock_put_hourly.assert_any_call(fake_table, "1:Main", "2026-08-20T13:00:00Z", "Main", 42.0)
    mock_put_latest.assert_any_call(fake_table, "1:1", "Kitchen", 9.4)
    mock_put_latest.assert_any_call(fake_table, "1:2", "Garage", 3.1)
    mock_put_latest.assert_any_call(fake_table, "1:Main", "Main", 42.0)


def test_poll_and_store_skips_failing_channel_without_blocking_others():
    channels = [
        {"circuit_id": "1:1", "name": "Kitchen", "channel_obj": MagicMock()},
        {"circuit_id": "1:2", "name": "Garage", "channel_obj": MagicMock()},
    ]
    fake_vue = MagicMock()
    fake_table = MagicMock()
    hour_start = datetime.datetime(2026, 8, 20, 13, 0, 0)
    hour_end = datetime.datetime(2026, 8, 20, 14, 0, 0)

    def fail_first(*args, **kwargs):
        if not fail_first.called:
            fail_first.called = True
            raise RuntimeError("boom")
        return 3.1
    fail_first.called = False

    with patch("poller.handler.emporia_client.fetch_hour_max_amps", side_effect=fail_first), \
         patch("poller.handler.emporia_client.fetch_main_hour_max_amps", return_value=42.0), \
         patch("poller.handler.dynamo.put_hourly_max") as mock_put_hourly, \
         patch("poller.handler.dynamo.put_latest_reading"):
        stored = handler.poll_and_store(fake_vue, fake_table, channels, 1, hour_start, hour_end)

    assert stored == 2  # one circuit failed, but Garage + Main still stored
    mock_put_hourly.assert_any_call(fake_table, "1:2", "2026-08-20T13:00:00Z", "Garage", 3.1)
    mock_put_hourly.assert_any_call(fake_table, "1:Main", "2026-08-20T13:00:00Z", "Main", 42.0)


def test_poll_and_store_skips_main_when_device_gid_is_none():
    fake_vue = MagicMock()
    fake_table = MagicMock()
    hour_start = datetime.datetime(2026, 8, 20, 13, 0, 0)
    hour_end = datetime.datetime(2026, 8, 20, 14, 0, 0)

    with patch("poller.handler.emporia_client.fetch_main_hour_max_amps") as mock_main, \
         patch("poller.handler.dynamo.put_hourly_max"), \
         patch("poller.handler.dynamo.put_latest_reading"):
        stored = handler.poll_and_store(fake_vue, fake_table, [], None, hour_start, hour_end)

    mock_main.assert_not_called()
    assert stored == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_poller_handler.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`src/poller/handler.py`:
```python
import datetime
import os

from shared import dynamo, emporia_client, secrets


def previous_hour_bounds(now=None):
    now = now or datetime.datetime.utcnow()
    hour_end = now.replace(minute=0, second=0, microsecond=0)
    hour_start = hour_end - datetime.timedelta(hours=1)
    return hour_start, hour_end


def hour_str(hour_start):
    return hour_start.strftime("%Y-%m-%dT%H:00:00Z")


def poll_and_store(vue, table, channels, device_gid, hour_start, hour_end):
    """Fetch the true hourly max for every individual channel plus the
    combined Main (mains-legs-summed) reading, and write them to DynamoDB.
    A single channel's failure is logged and skipped — it must not block
    the rest of the circuits or the hourly run."""
    hstr = hour_str(hour_start)
    stored = 0
    for ch in channels:
        try:
            max_amps = emporia_client.fetch_hour_max_amps(
                vue, ch["channel_obj"], hour_start, hour_end
            )
        except Exception as exc:
            print(f"Failed to fetch hour data for {ch['circuit_id']}: {exc}")
            continue
        dynamo.put_hourly_max(table, ch["circuit_id"], hstr, ch["name"], max_amps)
        dynamo.put_latest_reading(table, ch["circuit_id"], ch["name"], max_amps)
        stored += 1

    if device_gid is not None:
        try:
            main_amps = emporia_client.fetch_main_hour_max_amps(
                vue, device_gid, hour_start, hour_end
            )
        except Exception as exc:
            print(f"Failed to fetch Main hour data: {exc}")
        else:
            main_circuit_id = f"{device_gid}:{emporia_client.WHOLE_HOME_NAME}"
            dynamo.put_hourly_max(table, main_circuit_id, hstr, emporia_client.WHOLE_HOME_NAME, main_amps)
            dynamo.put_latest_reading(table, main_circuit_id, emporia_client.WHOLE_HOME_NAME, main_amps)
            stored += 1

    return stored


def lambda_handler(event, context):
    email, password = secrets.get_emporia_credentials()
    table = dynamo.get_table(os.environ["TABLE_NAME"])

    vue = emporia_client.login(email, password)
    channels = emporia_client.fetch_channels(vue)
    device_gid = channels[0]["device_gid"] if channels else None

    hour_start, hour_end = previous_hour_bounds()
    stored = poll_and_store(vue, table, channels, device_gid, hour_start, hour_end)

    print(f"Stored {stored}/{len(channels) + 1} circuits for {hour_str(hour_start)}")
    return {"stored": stored, "total_channels": len(channels) + 1, "hour": hour_str(hour_start)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_poller_handler.py -v`
Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/poller/handler.py tests/test_poller_handler.py
git commit -m "feat: add poller Lambda handler"
```

---

### Task 6: Web Lambda (routing, history/live API, payload builders)

**Files:**
- Create: `src/web/__init__.py` (already created in Task 1 — verify it's empty and present)
- Create: `src/web/handler.py`
- Create: `tests/test_web_handler.py`

**Interfaces:**
- Consumes: `emporia_client.login`, `emporia_client.fetch_channels`, `emporia_client.fetch_live_amps`, `emporia_client.compute_main_from_readings`, `emporia_client.WHOLE_HOME_NAME`, `dynamo.get_table`, `dynamo.get_latest_readings`, `dynamo.get_history`, `secrets.get_emporia_credentials` (from Tasks 1-4); `web.page.PAGE_HTML` (from Task 7 — this task can be implemented before Task 7 since the import is deferred inside the route handler, see Step 3)
- Produces: `handler.build_history_payload(latest_items, whole_home_history) -> dict`, `handler.build_live_payload(readings_by_circuit) -> dict`, `handler.find_whole_home_circuit_id(latest_items) -> str | None`, `handler.since_iso(now=None) -> str`, `handler.handle_history_request(table) -> dict`, `handler.handle_live_request(email, password) -> dict`, `handler.lambda_handler(event, context) -> dict`

- [ ] **Step 1: Write the failing tests**

`tests/test_web_handler.py`:
```python
import os, json, datetime
from unittest.mock import MagicMock, patch
from web import handler


def test_build_history_payload_shapes_circuits_and_history():
    latest_items = [
        {"PK": "1:1", "name": "Kitchen", "amps": 9.4, "updated_at": 1000},
        {"PK": "1:2", "name": "Main", "amps": 42.0, "updated_at": 1000},
    ]
    whole_home_history = [
        {"SK": "2026-08-20T13:00:00Z", "max_amps": 40.0},
        {"SK": "2026-08-20T14:00:00Z", "max_amps": 45.5},
    ]

    payload = handler.build_history_payload(latest_items, whole_home_history)

    assert payload == {
        "circuits": [
            {"circuit_id": "1:1", "name": "Kitchen", "amps": 9.4, "updated_at": 1000},
            {"circuit_id": "1:2", "name": "Main", "amps": 42.0, "updated_at": 1000},
        ],
        "history": [
            {"hour": "2026-08-20T13:00:00Z", "amps": 40.0},
            {"hour": "2026-08-20T14:00:00Z", "amps": 45.5},
        ],
    }


def test_find_whole_home_circuit_id_matches_main():
    items = [
        {"PK": "1:1", "name": "Kitchen"},
        {"PK": "1:Main", "name": "Main"},
    ]
    assert handler.find_whole_home_circuit_id(items) == "1:Main"


def test_find_whole_home_circuit_id_none_when_absent():
    items = [{"PK": "1:1", "name": "Kitchen"}]
    assert handler.find_whole_home_circuit_id(items) is None


def test_since_iso_is_30_days_before_now():
    now = datetime.datetime(2026, 8, 20, 14, 37)
    assert handler.since_iso(now) == "2026-07-21T14:00:00Z"


def test_handle_history_request_skips_history_query_when_no_whole_home_circuit():
    fake_table = MagicMock()
    with patch("web.handler.dynamo.get_latest_readings", return_value=[
        {"PK": "1:1", "name": "Kitchen", "amps": 5.0, "updated_at": 1},
    ]), patch("web.handler.dynamo.get_history") as mock_get_history:
        result = handler.handle_history_request(fake_table)

    mock_get_history.assert_not_called()
    assert result["history"] == []


def test_lambda_handler_routes_root_to_html():
    event = {"requestContext": {"http": {"method": "GET"}}, "rawPath": "/"}
    response = handler.lambda_handler(event, None)
    assert response["statusCode"] == 200
    assert response["headers"]["Content-Type"] == "text/html"


def test_lambda_handler_routes_unknown_path_to_404():
    event = {"requestContext": {"http": {"method": "GET"}}, "rawPath": "/nope"}
    response = handler.lambda_handler(event, None)
    assert response["statusCode"] == 404


def test_lambda_handler_returns_clean_error_when_live_request_fails():
    event = {"requestContext": {"http": {"method": "GET"}}, "rawPath": "/api/live"}
    with patch("web.handler.secrets.get_emporia_credentials", return_value=("a@b.com", "x")), \
         patch("web.handler.handle_live_request", side_effect=RuntimeError("emporia login failed")):
        response = handler.lambda_handler(event, None)

    assert response["statusCode"] == 502
    body = json.loads(response["body"])
    assert "error" in body
    assert "emporia login failed" not in body["error"].lower()  # no raw exception text leaked


def test_lambda_handler_routes_history_and_returns_json():
    event = {"requestContext": {"http": {"method": "GET"}}, "rawPath": "/api/history"}
    with patch.dict(os.environ, {"TABLE_NAME": "EmporiaReadings"}), \
         patch("web.handler.dynamo.get_table", return_value=MagicMock()), \
         patch("web.handler.handle_history_request", return_value={"circuits": [], "history": []}):
        response = handler.lambda_handler(event, None)
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"circuits": [], "history": []}
```

Note: `test_lambda_handler_routes_root_to_html` requires `src/web/page.py` to exist with a `PAGE_HTML` string. If running this task before Task 7, create a temporary placeholder first: `echo 'PAGE_HTML = "<html></html>"' > src/web/page.py` (Task 7 will replace it with the real page — do not commit a placeholder, see Task 7 Step 1).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_web_handler.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`src/web/handler.py`:
```python
import datetime
import json
import os

from shared import dynamo, emporia_client, secrets

HISTORY_DAYS = 30


def build_history_payload(latest_items, whole_home_history):
    return {
        "circuits": [
            {
                "circuit_id": item["PK"],
                "name": item["name"],
                "amps": float(item["amps"]),
                "updated_at": int(item["updated_at"]),
            }
            for item in latest_items
        ],
        "history": [
            {"hour": row["SK"], "amps": float(row["max_amps"])}
            for row in whole_home_history
        ],
    }


def build_live_payload(readings_by_circuit):
    return {
        "circuits": [
            {"circuit_id": cid, "name": info["name"], "amps": info["amps"]}
            for cid, info in readings_by_circuit.items()
        ]
    }


def find_whole_home_circuit_id(latest_items):
    """The poller always names the combined mains reading exactly 'Main'
    (see shared.emporia_client.WHOLE_HOME_NAME) — no fuzzy matching needed,
    we control the naming ourselves."""
    for item in latest_items:
        if item["name"] == emporia_client.WHOLE_HOME_NAME:
            return item["PK"]
    return None


def since_iso(now=None):
    now = now or datetime.datetime.utcnow()
    since = now - datetime.timedelta(days=HISTORY_DAYS)
    return since.strftime("%Y-%m-%dT%H:00:00Z")


def handle_history_request(table):
    latest_items = dynamo.get_latest_readings(table)
    whole_home_id = find_whole_home_circuit_id(latest_items)
    history = dynamo.get_history(table, whole_home_id, since_iso()) if whole_home_id else []
    return build_history_payload(latest_items, history)


def handle_live_request(email, password):
    vue = emporia_client.login(email, password)
    channels = emporia_client.fetch_channels(vue)  # already excludes the combined multi-leg channel
    device_gids = list({ch["device_gid"] for ch in channels})
    readings = emporia_client.fetch_live_amps(vue, device_gids)

    by_circuit = {}
    for ch in channels:
        if ch["circuit_id"] in readings:
            by_circuit[ch["circuit_id"]] = {
                "name": ch["name"],
                "amps": readings[ch["circuit_id"]],
            }

    for gid in device_gids:
        main_amps = emporia_client.compute_main_from_readings(readings, gid)
        if main_amps is not None:
            by_circuit[f"{gid}:{emporia_client.WHOLE_HOME_NAME}"] = {
                "name": emporia_client.WHOLE_HOME_NAME,
                "amps": main_amps,
            }

    return build_live_payload(by_circuit)


def _json_response(payload):
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }


def _html_response(html):
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/html"},
        "body": html,
    }


def _not_found():
    return {"statusCode": 404, "headers": {"Content-Type": "text/plain"}, "body": "Not Found"}


def _error_response(message):
    return {
        "statusCode": 502,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": message}),
    }


def lambda_handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/")

    if method == "GET" and path == "/":
        from web.page import PAGE_HTML
        return _html_response(PAGE_HTML)

    if method == "GET" and path == "/api/history":
        table = dynamo.get_table(os.environ["TABLE_NAME"])
        return _json_response(handle_history_request(table))

    if method == "GET" and path == "/api/live":
        email, password = secrets.get_emporia_credentials()
        try:
            payload = handle_live_request(email, password)
        except Exception as exc:
            print(f"Live request failed: {exc}")
            return _error_response("Live reading unavailable — Emporia request failed")
        return _json_response(payload)

    return _not_found()
```

Per the spec's Error Handling section: if Emporia login/fetch fails on `/api/live`, the route must return a clear error rather than a raw stack trace (a bare exception would otherwise propagate to Lambda's default 500 handler, which includes exception details in the body) — `_error_response` returns a fixed, non-leaking message instead.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_web_handler.py -v`
Expected: 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/web/handler.py tests/test_web_handler.py
git commit -m "feat: add web Lambda handler with history/live routes"
```

---

### Task 7: Frontend page

**Files:**
- Create: `src/web/page.py` (replaces the Task 6 placeholder if one was created)
- Create: `tests/test_page.py`

**Interfaces:**
- Produces: `web.page.PAGE_HTML: str`
- Consumed by: `web.handler.lambda_handler` (Task 6, `GET /` route)

- [ ] **Step 1: Write the failing tests**

`tests/test_page.py`:
```python
from web.page import PAGE_HTML


def test_page_has_no_watts_references():
    assert "watt" not in PAGE_HTML.lower()


def test_page_has_expected_elements():
    for expected in ("circuitsTable", "circuitsBody", "liveToggle", "historyChart"):
        assert expected in PAGE_HTML


def test_page_fetches_the_real_api_routes():
    assert '"/api/history"' in PAGE_HTML
    assert '"/api/live"' in PAGE_HTML
```

- [ ] **Step 2: Run tests to verify they fail (or pass trivially against the Task 6 placeholder, then fail on content checks)**

Run: `pytest tests/test_page.py -v`
Expected: FAIL — the placeholder (if present) lacks `circuitsTable`/`liveToggle`/etc., or the module doesn't exist yet if Task 6 was done differently

- [ ] **Step 3: Implement the real page**

`src/web/page.py`:
```python
PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Emporia Lite</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; color: #111; }
  h1 { font-size: 1.25rem; margin-bottom: 0.25rem; }
  table { width: 100%; border-collapse: collapse; margin: 1rem 0; }
  th, td { text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #ddd; }
  th { font-weight: 600; }
  .amps { font-variant-numeric: tabular-nums; text-align: right; }
  tr.main-row { font-weight: 700; }
  #liveToggleRow { margin: 1rem 0; font-size: 0.9rem; color: #555; }
  #status { font-size: 0.8rem; color: #888; margin-left: 0.5rem; }
  canvas { max-width: 100%; }
</style>
</head>
<body>
  <h1>Emporia Lite</h1>
  <table id="circuitsTable">
    <thead><tr><th>Circuit</th><th class="amps">Amps</th></tr></thead>
    <tbody id="circuitsBody"></tbody>
  </table>
  <div id="liveToggleRow">
    <label><input type="checkbox" id="liveToggle"> Live reading (updates every 5s)</label>
    <span id="status"></span>
  </div>
  <canvas id="historyChart" height="80"></canvas>

  <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2"></script>
  <script>
    let circuits = {}; // circuit_id -> {name, amps}
    let chart = null;
    let liveTimer = null;

    function renderTable() {
      const body = document.getElementById("circuitsBody");
      body.innerHTML = "";
      const all = Object.values(circuits);
      const main = all.filter(c => c.name === "Main");
      const rest = all.filter(c => c.name !== "Main").sort((a, b) => a.name.localeCompare(b.name));
      [...main, ...rest].forEach(c => {
        const row = document.createElement("tr");
        if (c.name === "Main") row.className = "main-row";
        row.innerHTML = `<td>${c.name}</td><td class="amps">${c.amps.toFixed(1)} A</td>`;
        body.appendChild(row);
      });
    }

    function renderChart(history) {
      const ctx = document.getElementById("historyChart");
      const labels = history.map(h => h.hour);
      const data = history.map(h => h.amps);
      if (chart) { chart.destroy(); }
      chart = new Chart(ctx, {
        type: "line",
        data: { labels, datasets: [{ label: "Main (amps)", data, borderWidth: 1.5, pointRadius: 0 }] },
        options: {
          animation: false,
          scales: { y: { beginAtZero: true, title: { display: true, text: "Amps" } } },
          plugins: { zoom: { pan: { enabled: true, mode: "x" }, zoom: { wheel: { enabled: true }, mode: "x" } } }
        }
      });
    }

    async function loadHistory() {
      const res = await fetch("/api/history");
      const data = await res.json();
      circuits = {};
      data.circuits.forEach(c => { circuits[c.circuit_id] = c; });
      renderTable();
      renderChart(data.history);
      document.getElementById("status").textContent = "Loaded " + new Date().toLocaleTimeString();
    }

    async function pollLive() {
      try {
        const res = await fetch("/api/live");
        if (!res.ok) throw new Error("live request failed");
        const data = await res.json();
        data.circuits.forEach(c => { circuits[c.circuit_id] = c; });
        renderTable();
        document.getElementById("status").textContent = "Live " + new Date().toLocaleTimeString();
      } catch (e) {
        // Fall back to whatever's already rendered (last known LATEST reading)
        // instead of breaking the page on a transient Emporia/API failure.
        document.getElementById("status").textContent = "Live update failed, showing last known reading";
      }
    }

    document.getElementById("liveToggle").addEventListener("change", (e) => {
      if (e.target.checked) {
        pollLive();
        liveTimer = setInterval(pollLive, 5000);
      } else {
        clearInterval(liveTimer);
        loadHistory();
      }
    });

    loadHistory();
  </script>
</body>
</html>
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_page.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Run the full test suite to confirm nothing regressed**

Run: `pytest -v`
Expected: 36 tests PASS (running total by file: `test_emporia_client.py` 14, `test_secrets.py` 2, `test_dynamo.py` 3, `test_poller_handler.py` 5, `test_web_handler.py` 9, `test_page.py` 3 — Task 1's 5 amps-math tests are a subset of `test_emporia_client.py`'s 14, added to in Task 2)

- [ ] **Step 6: Commit**

```bash
git add src/web/page.py tests/test_page.py
git commit -m "feat: add frontend page (live table + 30-day history graph)"
```

---

### Task 8: SAM template, Secrets Manager secret, and deploy prep

**Files:**
- Create: `template.yaml`
- Create: `README.md`
- Create: `scripts/verify_amphours.py`

**Interfaces:**
- Consumes: all `src/` modules from Tasks 1-7 (packaged as-is, no changes)
- Produces: a deployable SAM template; a Secrets Manager secret (created via AWS CLI, not code)

- [ ] **Step 1: Write the SAM template**

`template.yaml`:
```yaml
AWSTemplateFormatVersion: '2010-09-09'
Transform: AWS::Serverless-2016-10-31
Description: Emporia Lite — serverless amps-only power dashboard

Parameters:
  EmporiaSecretArn:
    Type: String
    Description: ARN of the Secrets Manager secret holding EMPORIA_EMAIL and EMPORIA_PASSWORD

Globals:
  Function:
    Runtime: python3.12
    Timeout: 30
    MemorySize: 256
    Environment:
      Variables:
        TABLE_NAME: !Ref ReadingsTable
        EMPORIA_SECRET_ARN: !Ref EmporiaSecretArn

Resources:
  ReadingsTable:
    Type: AWS::DynamoDB::Table
    Properties:
      TableName: EmporiaReadings
      BillingMode: PAY_PER_REQUEST
      AttributeDefinitions:
        - AttributeName: PK
          AttributeType: S
        - AttributeName: SK
          AttributeType: S
      KeySchema:
        - AttributeName: PK
          KeyType: HASH
        - AttributeName: SK
          KeyType: RANGE
      TimeToLiveSpecification:
        AttributeName: ttl
        Enabled: true

  PollerFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: src/
      Handler: poller.handler.lambda_handler
      Policies:
        - DynamoDBCrudPolicy:
            TableName: !Ref ReadingsTable
        - Statement:
            - Effect: Allow
              Action: secretsmanager:GetSecretValue
              Resource: !Ref EmporiaSecretArn
      Events:
        HourlySchedule:
          Type: Schedule
          Properties:
            Schedule: rate(1 hour)

  WebFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: src/
      Handler: web.handler.lambda_handler
      Policies:
        - DynamoDBReadPolicy:
            TableName: !Ref ReadingsTable
        - Statement:
            - Effect: Allow
              Action: secretsmanager:GetSecretValue
              Resource: !Ref EmporiaSecretArn
      FunctionUrlConfig:
        AuthType: NONE

Outputs:
  WebFunctionUrl:
    Description: Public URL for the dashboard
    # SAM auto-creates an AWS::Lambda::Url resource for a function that sets
    # FunctionUrlConfig, with logical ID "<FunctionLogicalId>Url" — for
    # WebFunction that's WebFunctionUrl, and its ARN attribute is FunctionUrl.
    Value: !GetAtt WebFunctionUrl.FunctionUrl
```

- [ ] **Step 2: Install the SAM CLI if not already present**

```bash
brew install aws-sam-cli
sam --version
```
Expected: prints a version string (e.g. `SAM CLI, version 1.x`)

- [ ] **Step 3: Validate the template**

```bash
sam validate --lint
```
Expected: `template.yaml is a valid SAM Template`. Fix any reported errors before continuing — do not proceed to deploy with an invalid template.

- [ ] **Step 4: Create the Secrets Manager secret from the family tracker's existing credentials**

The family tracker currently reads Emporia credentials from a local `.env` file at `/Users/zach/2026/personal/projects/emporia-family-tracker/.env` (keys `EMPORIA_EMAIL`, `EMPORIA_PASSWORD`) — not from Secrets Manager. This creates a new, dedicated secret for this app, seeded from those same values, without ever typing the plaintext credentials into a command or committing them anywhere:

```bash
aws secretsmanager create-secret \
  --name emporia-lite/credentials \
  --secret-string "$(python3 -c "
import json
from dotenv import dotenv_values
env = dotenv_values('/Users/zach/2026/personal/projects/emporia-family-tracker/.env')
print(json.dumps({'EMPORIA_EMAIL': env['EMPORIA_EMAIL'], 'EMPORIA_PASSWORD': env['EMPORIA_PASSWORD']}))
")"
```

Note the returned `ARN` from this command's output — it's needed for the `EmporiaSecretArn` parameter in Task 9.

- [ ] **Step 5: Write the README**

`README.md`:
```markdown
# Emporia Lite

Single-household Emporia power dashboard. Amps only (no watts), real per-circuit
current draw, true hourly max (not average). Serverless: 2 Lambda functions,
1 DynamoDB table, no EC2/ALB/NAT/S3. See
`docs/superpowers/specs/2026-08-20-emporia-lite-remodel-design.md` for the
full design.

## Local development

\```bash
pip install -r requirements-dev.txt
pytest
\```

## Deploy

\```bash
sam build
sam deploy --guided   # first time only; then just `sam deploy`
\```

Requires a Secrets Manager secret (ARN passed as the `EmporiaSecretArn`
parameter) containing:

\```json
{"EMPORIA_EMAIL": "...", "EMPORIA_PASSWORD": "..."}
\```
```

- [ ] **Step 6: Write the diagnostic verification script (optional tool, not gating — AmpHours behavior was already confirmed live during design)**

`scripts/verify_amphours.py`:
```python
"""Diagnostic script: confirms Emporia's AmpHours unit returns real,
plausible data for this account. Not part of the deployed app — run
manually against a local .env with EMPORIA_EMAIL/EMPORIA_PASSWORD if you
ever need to re-check this against a different Emporia account or after a
pyemvue upgrade.

Usage:
    pip install -r requirements-dev.txt
    EMPORIA_EMAIL=... EMPORIA_PASSWORD=... python scripts/verify_amphours.py
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from shared import emporia_client  # noqa: E402


def main():
    email = os.environ["EMPORIA_EMAIL"]
    password = os.environ["EMPORIA_PASSWORD"]

    print("Logging in...")
    vue = emporia_client.login(email, password)
    print("Login OK")

    channels = emporia_client.fetch_channels(vue)
    print(f"Found {len(channels)} real circuits (combined multi-leg channel excluded)")
    for ch in channels[:8]:
        print(f"  {ch['circuit_id']}  name={ch['name']!r}")

    now = datetime.datetime.now(datetime.timezone.utc)
    hour_end = now.replace(minute=0, second=0, microsecond=0)
    hour_start = hour_end - datetime.timedelta(hours=1)

    print(f"\nLast hour ({hour_start} to {hour_end}), true hourly max for first 5 circuits:")
    for ch in channels[:5]:
        try:
            max_amps = emporia_client.fetch_hour_max_amps(vue, ch["channel_obj"], hour_start, hour_end)
            print(f"  {ch['name']:<25} max_amps={max_amps:.2f}")
        except Exception as exc:
            print(f"  {ch['name']:<25} ERROR: {exc}")

    device_gid = channels[0]["device_gid"] if channels else None
    if device_gid is not None:
        main_amps = emporia_client.fetch_main_hour_max_amps(vue, device_gid, hour_start, hour_end)
        print(f"\nCombined Main (mains legs summed) hourly max: {main_amps:.2f} A")

    print("\nLive reading:")
    device_gids = list({ch["device_gid"] for ch in channels})
    live = emporia_client.fetch_live_amps(vue, device_gids)
    for cid, amps in list(live.items())[:8]:
        print(f"  {cid:<20} {amps:.2f} A")
    if device_gid is not None:
        main_live = emporia_client.compute_main_from_readings(live, device_gid)
        print(f"  Main (live, legs summed): {main_live}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Commit**

```bash
git add template.yaml README.md scripts/verify_amphours.py
git commit -m "feat: add SAM template and deploy prep"
```

---

### Task 9: First deploy and smoke test

**Files:** none (deployment/verification only)

**Interfaces:** none — this task exercises the deployed system end-to-end

- [ ] **Step 1: Build**

```bash
sam build
```
Expected: `Build Succeeded`

- [ ] **Step 2: Deploy, guided (first time)**

```bash
sam deploy --guided
```
When prompted:
- Stack Name: `emporia-lite`
- AWS Region: `us-east-1`
- Parameter `EmporiaSecretArn`: paste the ARN from Task 8 Step 4
- Confirm changes before deploy: `Y`
- Allow SAM CLI to create IAM roles: `Y`
- Save arguments to `samconfig.toml`: `Y`

Expected: deploy succeeds, `Outputs` section prints a `WebFunctionUrl` ending in `.lambda-url.us-east-1.on.aws/`

- [ ] **Step 3: Manually invoke the poller once (don't wait up to an hour for the first EventBridge trigger)**

```bash
aws lambda invoke --function-name $(aws cloudformation describe-stack-resources --stack-name emporia-lite --logical-resource-id PollerFunction --query 'StackResources[0].PhysicalResourceId' --output text) /tmp/poller-output.json
cat /tmp/poller-output.json
```
Expected: JSON like `{"stored": 17, "total_channels": 17, "hour": "..."}` (16 real circuits + 1 Main, matching the design verification — the exact circuit count may differ slightly from a fresh account state, but `stored` should be within 1-2 of `total_channels`, not 0)

- [ ] **Step 4: Verify DynamoDB was populated**

```bash
aws dynamodb scan --table-name EmporiaReadings --max-items 5
```
Expected: at least a few items returned, including one with `"name": {"S": "Main"}`

- [ ] **Step 5: Hit the web routes directly**

```bash
WEB_URL=$(aws cloudformation describe-stacks --stack-name emporia-lite --query "Stacks[0].Outputs[?OutputKey=='WebFunctionUrl'].OutputValue" --output text)
curl -s "${WEB_URL}api/history" | python3 -m json.tool
curl -s "${WEB_URL}api/live" | python3 -m json.tool
```
Expected: both return valid JSON with a `circuits` array containing real circuit names and amp values, `/api/history` also has a non-empty `history` array once the poller from Step 3 has run

- [ ] **Step 6: Open the dashboard in a browser and verify the golden path**

```bash
open "$WEB_URL"
```
Manually verify:
- The live table shows real circuit names with plausible amp values, "Main" pinned to the top
- The 30-day graph renders (will show a single data point until more hourly polls accumulate — that's expected on a fresh deploy, not a bug)
- Checking "Live reading" causes the table to refresh every ~5 seconds with fresh values from `/api/live`
- Unchecking it stops the refresh and the page returns to the cached `/api/history` data
- No watts anywhere on the page

- [ ] **Step 7: Confirm the hourly schedule is wired up**

```bash
aws events list-rules --name-prefix emporia-lite
```
Expected: a rule with `"State": "ENABLED"` and a `rate(1 hour)` schedule targeting the poller function

- [ ] **Step 8: Commit the generated `samconfig.toml` deploy config exclusion (already gitignored) and do a final status check**

```bash
git status
```
Expected: clean working tree (no uncommitted changes — `samconfig.toml` and `.aws-sam/` are gitignored from Task 1)
