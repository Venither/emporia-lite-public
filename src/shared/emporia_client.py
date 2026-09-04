import pyemvue
from pyemvue.device import VueDeviceChannel
from pyemvue.enums import Scale, Unit

SCALE_SECONDS = 1  # matches Scale.SECOND
MAINS_LEGS = ("Mains_A", "Mains_B")
WHOLE_HOME_NAME = "Main"


def amphours_to_amps(amp_hours, scale_seconds=SCALE_SECONDS):
    if not amp_hours or amp_hours < 0:
        return 0.0
    return amp_hours * (3600 / scale_seconds)


def max_amps_from_series(amp_hours_series, scale_seconds=SCALE_SECONDS):
    amps = [amphours_to_amps(v, scale_seconds) for v in amp_hours_series if v is not None]
    return max(amps) if amps else 0.0


def login(email, password):
    vue = pyemvue.PyEmVue()
    vue.login(username=email, password=password)
    return vue


def fetch_channels(vue):
    """vue.get_devices() returns VueDevice objects whose .channels is a LIST of
    VueDeviceChannel (unlike the usage-dict responses from get_device_list_usage,
    where .channels is a dict) — see pyemvue/device.py VueDevice.channels.

    Excludes Emporia's own combined multi-leg channel (channel_num containing
    a comma, e.g. "1,2,3") — its API rejects AmpHours for that channel, and
    the whole-home total is computed separately from the two mains legs
    instead (see fetch_main_hour_max_amps / compute_main_from_readings)."""
    devices = vue.get_devices()
    channels = []
    for device in devices:
        if device is None or not device.channels:
            continue
        for channel in device.channels:
            if channel is None:
                continue
            ch_num = channel.channel_num
            if "," in ch_num:
                continue
            circuit_id = f"{device.device_gid}:{ch_num}"
            name = channel.name or f"Circuit {ch_num}"
            channels.append({
                "circuit_id": circuit_id,
                "name": name,
                "device_gid": device.device_gid,
                "channel_num": ch_num,
                "channel_obj": channel,
            })
    return channels


def mains_leg_channels(device_gid):
    """The two incoming split-phase service legs. Not enumerable via
    get_devices() — Emporia treats them as special channels, addressed
    directly by name. Individually queryable for amps; the combined mains
    channel is not (Emporia's API rejects AmpHours for it outright)."""
    return [
        {
            "circuit_id": f"{device_gid}:{leg}",
            "name": leg,
            "device_gid": device_gid,
            "channel_num": leg,
            "channel_obj": VueDeviceChannel(gid=device_gid, name=leg, channelNum=leg),
        }
        for leg in MAINS_LEGS
    ]


def fetch_hour_max_amps(vue, channel_obj, hour_start, hour_end):
    usage_list, _ = vue.get_chart_usage(
        channel_obj,
        start=hour_start,
        end=hour_end,
        scale=Scale.SECOND.value,
        unit=Unit.AMPHOURS.value,
    )
    return max_amps_from_series(usage_list or [])


def fetch_main_hour_max_amps(vue, device_gid, hour_start, hour_end):
    """True hourly max for the combined whole-home ('Main') reading.

    Sums the two mains legs' per-second amp-hours readings together
    index-by-index (NOT each leg's individual max — the legs don't
    necessarily peak at the same moment, so max(A)+max(B) would overstate
    a peak that never actually happened simultaneously) and returns the max
    instantaneous amps of that combined series. See design doc for the 240V
    double-counting caveat inherent to summing split-phase legs."""
    leg_channels = mains_leg_channels(device_gid)
    leg_series = []
    for leg in leg_channels:
        usage_list, _ = vue.get_chart_usage(
            leg["channel_obj"],
            start=hour_start,
            end=hour_end,
            scale=Scale.SECOND.value,
            unit=Unit.AMPHOURS.value,
        )
        leg_series.append(usage_list or [])

    if len(leg_series) != 2 or not leg_series[0] or not leg_series[1]:
        return 0.0

    combined = [(a or 0) + (b or 0) for a, b in zip(*leg_series)]
    return max_amps_from_series(combined)


def fetch_live_amps(vue, device_gids):
    """max_retry_attempts=1 disables pyemvue's internal retry loop. pyemvue
    retries whenever ANY channel in the response has usage=None — which is
    routine for the synthetic Mains_*/combined channels this app doesn't use —
    and its default backoff (5 attempts, 2s initial, 30s cap) can sleep ~30s
    on its own, meeting or exceeding the Lambda's 30s timeout and producing a
    raw timeout instead of this app's clean 502 error path. The loop below
    already skips null-usage channels, so the retry bought nothing here."""
    usage_dict = vue.get_device_list_usage(
        device_gids,
        None,
        Scale.SECOND.value,
        Unit.AMPHOURS.value,
        max_retry_attempts=1,
    )
    readings = {}
    for gid, device in usage_dict.items():
        if device is None:
            continue
        for ch in (device.channels or {}).values():
            if ch is None or ch.usage is None:
                continue
            circuit_id = f"{gid}:{ch.channel_num}"
            readings[circuit_id] = round(amphours_to_amps(ch.usage), 1)
    return readings


def compute_main_from_readings(readings, device_gid):
    """Given a {circuit_id: amps} dict as returned by fetch_live_amps (which
    already includes Mains_A/Mains_B — get_device_list_usage returns them
    even though get_devices() doesn't enumerate them), sum the two legs into
    a single whole-home reading. Returns None if either leg is missing."""
    a = readings.get(f"{device_gid}:{MAINS_LEGS[0]}")
    b = readings.get(f"{device_gid}:{MAINS_LEGS[1]}")
    if a is None or b is None:
        return None
    return round(a + b, 1)
