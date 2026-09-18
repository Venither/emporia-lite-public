"""All reads and writes to the EmporiaReadings DynamoDB table go through
this file. One table holds three kinds of row per circuit, distinguished
by the sort key (SK): a dated hourly row (e.g. "2026-08-20T14:00:00Z"),
one "LATEST" row (the most recent reading), and one "ALL_TIME_MAX" row
(the highest reading ever seen). Every function here takes the table
object as its first argument, obtained by calling get_table()."""

import time
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

HISTORY_TTL_SECONDS = 30 * 24 * 3600  # 30 days
_END_OF_TIME_SK = "9999-12-31T23:59:59Z"
ALL_TIME_MAX_SK = "ALL_TIME_MAX"


def get_table(table_name):
    """Returns a boto3 Table resource — pass this into every other
    function in this file."""
    return boto3.resource("dynamodb").Table(table_name)


def put_hourly_max(table, circuit_id, hour, name, max_amps):
    """Writes one hour's true peak reading for a circuit. Auto-deleted
    after 30 days via the `ttl` attribute (DynamoDB's built-in expiry) —
    this is what feeds the 30-day history graph."""
    table.put_item(Item={
        "PK": circuit_id,
        "SK": hour,
        "name": name,
        "max_amps": Decimal(str(round(max_amps, 2))),
        "ttl": int(time.time()) + HISTORY_TTL_SECONDS,
    })


def put_latest_reading(table, circuit_id, name, amps):
    """Overwrites the single "current reading" row for a circuit — this is
    what the dashboard table shows on page load. No TTL: it's always
    overwritten by the next poll, never left to expire."""
    table.put_item(Item={
        "PK": circuit_id,
        "SK": "LATEST",
        "name": name,
        "amps": Decimal(str(round(amps, 2))),
        "updated_at": int(time.time()),
    })


def update_all_time_max(table, circuit_id, name, amps):
    """Atomically update the ALL_TIME_MAX row for a circuit only if `amps`
    exceeds the currently stored value (or none exists yet). No TTL — unlike
    hourly rows, this one is never dropped, so it survives past the 30-day
    history window. The ConditionExpression makes the compare-and-set atomic
    at the DynamoDB layer rather than racing a separate read + write."""
    try:
        table.update_item(
            Key={"PK": circuit_id, "SK": ALL_TIME_MAX_SK},
            UpdateExpression="SET max_amps = :amps, #n = :name, updated_at = :ts",
            ConditionExpression="attribute_not_exists(max_amps) OR max_amps < :amps",
            ExpressionAttributeNames={"#n": "name"},
            ExpressionAttributeValues={
                ":amps": Decimal(str(round(amps, 2))),
                ":name": name,
                ":ts": int(time.time()),
            },
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        # Expected, common case: the new reading didn't beat the existing
        # all-time max, so there's nothing to update.


def _scan_by_sk(table, sk_value):
    """Full-table scan filtered to a specific SK value.

    Paginated: DynamoDB applies its 1MB page limit BEFORE the filter
    expression, so this scan walks every hourly row in the table (~17
    circuits x 720 hourly rows over the 30-day TTL window). Once that
    crosses 1MB, a single un-paginated scan silently drops matching items —
    including possibly 'Main', which would empty the whole 30-day graph."""
    items = []
    kwargs = {"FilterExpression": Attr("SK").eq(sk_value)}
    while True:
        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            return items
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def get_latest_readings(table):
    """One row per circuit: its most recent reading. Feeds the dashboard's
    live table on page load."""
    return _scan_by_sk(table, "LATEST")


def get_all_time_maxes(table):
    """One row per circuit: the highest reading ever recorded for it."""
    return _scan_by_sk(table, ALL_TIME_MAX_SK)


def get_history(table, circuit_id, since_iso):
    """All hourly rows for one circuit from since_iso onward. The upper
    bound excludes the 'LATEST' item because the string 'LATEST' sorts
    after any ISO date string in DynamoDB's byte-order comparison."""
    response = table.query(
        KeyConditionExpression=Key("PK").eq(circuit_id)
        & Key("SK").between(since_iso, _END_OF_TIME_SK)
    )
    return response.get("Items", [])
