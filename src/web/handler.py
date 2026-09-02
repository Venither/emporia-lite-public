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
