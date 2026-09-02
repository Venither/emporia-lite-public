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
