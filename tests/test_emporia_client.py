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
