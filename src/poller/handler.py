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
