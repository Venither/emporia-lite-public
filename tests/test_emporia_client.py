from shared import emporia_client
from unittest.mock import MagicMock
import datetime


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

    start = datetime.datetime(2026, 8, 20, 14, 0)
    end = datetime.datetime(2026, 8, 20, 15, 0)

    result = emporia_client.fetch_main_hour_max_amps(fake_vue, 12345, start, end)

    assert abs(result - 7.0) < 0.001  # max(5+1, 1+6) = 7, not 5+6=11


def test_fetch_main_hour_max_amps_returns_zero_when_a_leg_is_empty():
    fake_vue = MagicMock()
    fake_vue.get_chart_usage.side_effect = [([1 / 3600], "start"), ([], "start")]
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


def test_fetch_live_amps_disables_pyemvue_internal_retry_loop():
    # pyemvue retries whenever ANY channel comes back with usage=None, with a
    # default backoff that can sleep ~30s -- past the Lambda's 30s timeout.
    # fetch_live_amps skips null-usage channels itself, so retries buy nothing.
    with_usage = MagicMock()
    with_usage.usage = 2 / 3600
    with_usage.channel_num = "1"

    without_usage = MagicMock()
    without_usage.usage = None
    without_usage.channel_num = "Mains_A"

    fake_device = MagicMock()
    fake_device.channels = {"1": with_usage, "Mains_A": without_usage}

    fake_vue = MagicMock()
    fake_vue.get_device_list_usage.return_value = {42: fake_device}

    result = emporia_client.fetch_live_amps(fake_vue, [42])

    assert result == {"42:1": 2.0}  # null-usage channel skipped, not retried
    _, kwargs = fake_vue.get_device_list_usage.call_args
    assert kwargs["max_retry_attempts"] == 1
