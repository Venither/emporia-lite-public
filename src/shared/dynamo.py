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
