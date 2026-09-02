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
