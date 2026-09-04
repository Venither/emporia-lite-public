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
    all_time_max_items = [
        {"PK": "1:1", "max_amps": 15.0},
        # no all-time-max row for 1:2 yet — must not crash, all_time_max is None
    ]

    payload = handler.build_history_payload(latest_items, whole_home_history, all_time_max_items)

    assert payload == {
        "circuits": [
            {"circuit_id": "1:1", "name": "Kitchen", "amps": 9.4, "updated_at": 1000, "all_time_max": 15.0},
            {"circuit_id": "1:2", "name": "Main", "amps": 42.0, "updated_at": 1000, "all_time_max": None},
        ],
        "history": [
            {"hour": "2026-08-20T13:00:00Z", "amps": 40.0},
            {"hour": "2026-08-20T14:00:00Z", "amps": 45.5},
        ],
    }


def test_build_history_payload_defaults_all_time_max_to_none_when_omitted():
    latest_items = [{"PK": "1:1", "name": "Kitchen", "amps": 9.4, "updated_at": 1000}]
    payload = handler.build_history_payload(latest_items, [])
    assert payload["circuits"][0]["all_time_max"] is None


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
    ]), patch("web.handler.dynamo.get_all_time_maxes", return_value=[]), \
         patch("web.handler.dynamo.get_history") as mock_get_history:
        result = handler.handle_history_request(fake_table)

    mock_get_history.assert_not_called()
    assert result["history"] == []


def test_handle_history_request_includes_all_time_max_per_circuit():
    fake_table = MagicMock()
    with patch("web.handler.dynamo.get_latest_readings", return_value=[
        {"PK": "1:1", "name": "Kitchen", "amps": 5.0, "updated_at": 1},
    ]), patch("web.handler.dynamo.get_all_time_maxes", return_value=[
        {"PK": "1:1", "max_amps": 30.0},
    ]), patch("web.handler.dynamo.get_history", return_value=[]):
        result = handler.handle_history_request(fake_table)

    assert result["circuits"][0]["all_time_max"] == 30.0


def _live_channels():
    """What emporia_client.fetch_channels really returns: the enumerable
    named circuits only — it already drops the combined '1,2,3' channel, and
    the Mains_* legs are never enumerated by get_devices() at all."""
    return [
        {"circuit_id": "42:1", "name": "Kitchen", "device_gid": 42, "channel_num": "1"},
        {"circuit_id": "42:2", "name": "Garage", "device_gid": 42, "channel_num": "2"},
    ]


def _live_readings():
    """What emporia_client.fetch_live_amps really returns: get_device_list_usage
    hands back the Mains_* legs and the combined channel too, none of which
    should reach the UI as circuits of their own."""
    return {
        "42:Mains_A": 10.0,
        "42:Mains_B": 12.0,
        "42:1,2,3": 22.0,
        "42:1": 5.0,
        "42:2": 3.0,
    }


def test_handle_live_request_returns_real_circuits_plus_synthesized_main():
    handler.reset_client_cache()
    with patch("web.handler.emporia_client.login", return_value=MagicMock()), \
         patch("web.handler.emporia_client.fetch_channels", return_value=_live_channels()), \
         patch("web.handler.emporia_client.fetch_live_amps", return_value=_live_readings()):
        payload = handler.handle_live_request("a@b.com", "x")

    by_id = {c["circuit_id"]: c for c in payload["circuits"]}

    assert set(by_id) == {"42:1", "42:2", "42:Main"}
    assert by_id["42:1"] == {"circuit_id": "42:1", "name": "Kitchen", "amps": 5.0}
    assert by_id["42:2"] == {"circuit_id": "42:2", "name": "Garage", "amps": 3.0}
    assert by_id["42:Main"] == {"circuit_id": "42:Main", "name": "Main", "amps": 22.0}

    # Nothing synthetic leaks through as a circuit of its own.
    names = {c["name"] for c in payload["circuits"]}
    assert "Mains_A" not in names and "Mains_B" not in names
    assert not any("," in cid for cid in by_id)


def test_handle_live_request_dedups_device_gids():
    handler.reset_client_cache()
    channels = _live_channels() + [
        {"circuit_id": "42:3", "name": "Office", "device_gid": 42, "channel_num": "3"},
    ]
    with patch("web.handler.emporia_client.login", return_value=MagicMock()), \
         patch("web.handler.emporia_client.fetch_channels", return_value=channels), \
         patch("web.handler.emporia_client.fetch_live_amps", return_value=_live_readings()) as mock_live:
        payload = handler.handle_live_request("a@b.com", "x")

    gids = mock_live.call_args[0][1]
    assert gids == [42]  # one call for the shared device, not one per channel

    # Only one synthesized Main, even though three channels share the gid.
    assert [c["name"] for c in payload["circuits"]].count("Main") == 1


def test_handle_live_request_skips_channels_with_no_live_reading():
    handler.reset_client_cache()
    readings = dict(_live_readings())
    del readings["42:2"]  # Garage reported no usage this instant
    with patch("web.handler.emporia_client.login", return_value=MagicMock()), \
         patch("web.handler.emporia_client.fetch_channels", return_value=_live_channels()), \
         patch("web.handler.emporia_client.fetch_live_amps", return_value=readings):
        payload = handler.handle_live_request("a@b.com", "x")

    assert {c["circuit_id"] for c in payload["circuits"]} == {"42:1", "42:Main"}


def test_handle_live_request_omits_main_when_a_mains_leg_is_missing():
    handler.reset_client_cache()
    readings = dict(_live_readings())
    del readings["42:Mains_B"]
    with patch("web.handler.emporia_client.login", return_value=MagicMock()), \
         patch("web.handler.emporia_client.fetch_channels", return_value=_live_channels()), \
         patch("web.handler.emporia_client.fetch_live_amps", return_value=readings):
        payload = handler.handle_live_request("a@b.com", "x")

    assert {c["circuit_id"] for c in payload["circuits"]} == {"42:1", "42:2"}


def test_get_cached_client_logs_in_only_once():
    handler.reset_client_cache()
    fake_vue = MagicMock()
    with patch("web.handler.emporia_client.login", return_value=fake_vue) as mock_login:
        first = handler.get_cached_client("a@b.com", "x")
        second = handler.get_cached_client("a@b.com", "x")

    assert first is second is fake_vue
    assert mock_login.call_count == 1


def test_reset_client_cache_forces_a_fresh_login():
    handler.reset_client_cache()
    with patch("web.handler.emporia_client.login", return_value=MagicMock()) as mock_login:
        handler.get_cached_client("a@b.com", "x")
        handler.reset_client_cache()
        handler.get_cached_client("a@b.com", "x")

    assert mock_login.call_count == 2


def test_failed_live_request_invalidates_the_cached_client():
    handler.reset_client_cache()
    event = {"requestContext": {"http": {"method": "GET"}}, "rawPath": "/api/live"}
    with patch("web.handler.secrets.get_emporia_credentials", return_value=("a@b.com", "x")), \
         patch("web.handler.emporia_client.login", return_value=MagicMock()), \
         patch("web.handler.emporia_client.fetch_channels", side_effect=RuntimeError("session expired")):
        response = handler.lambda_handler(event, None)

    assert response["statusCode"] == 502
    assert handler._client_cache == {}


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
