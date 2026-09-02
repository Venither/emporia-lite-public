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


def test_lambda_handler_returns_clean_error_when_credentials_fetch_fails():
    event = {"requestContext": {"http": {"method": "GET"}}, "rawPath": "/api/live"}
    with patch("web.handler.secrets.get_emporia_credentials", side_effect=RuntimeError("secret not found")):
        response = handler.lambda_handler(event, None)

    assert response["statusCode"] == 502
    body = json.loads(response["body"])
    assert "error" in body
    assert "secret not found" not in body["error"].lower()  # no raw exception text leaked


def test_lambda_handler_routes_history_and_returns_json():
    event = {"requestContext": {"http": {"method": "GET"}}, "rawPath": "/api/history"}
    with patch.dict(os.environ, {"TABLE_NAME": "EmporiaReadings"}), \
         patch("web.handler.dynamo.get_table", return_value=MagicMock()), \
         patch("web.handler.handle_history_request", return_value={"circuits": [], "history": []}):
        response = handler.lambda_handler(event, None)
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"circuits": [], "history": []}
