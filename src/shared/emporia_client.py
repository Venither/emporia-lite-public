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
