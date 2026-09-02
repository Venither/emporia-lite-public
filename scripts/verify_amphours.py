"""Diagnostic script: confirms Emporia's AmpHours unit returns real,
plausible data for this account. Not part of the deployed app — run
manually against a local .env with EMPORIA_EMAIL/EMPORIA_PASSWORD if you
ever need to re-check this against a different Emporia account or after a
pyemvue upgrade.

Usage:
    pip install -r requirements-dev.txt
    EMPORIA_EMAIL=... EMPORIA_PASSWORD=... python scripts/verify_amphours.py
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from shared import emporia_client  # noqa: E402


def main():
    email = os.environ["EMPORIA_EMAIL"]
    password = os.environ["EMPORIA_PASSWORD"]

    print("Logging in...")
    vue = emporia_client.login(email, password)
    print("Login OK")

    channels = emporia_client.fetch_channels(vue)
    print(f"Found {len(channels)} real circuits (combined multi-leg channel excluded)")
    for ch in channels[:8]:
        print(f"  {ch['circuit_id']}  name={ch['name']!r}")

    now = datetime.datetime.now(datetime.timezone.utc)
    hour_end = now.replace(minute=0, second=0, microsecond=0)
    hour_start = hour_end - datetime.timedelta(hours=1)

    print(f"\nLast hour ({hour_start} to {hour_end}), true hourly max for first 5 circuits:")
    for ch in channels[:5]:
        try:
            max_amps = emporia_client.fetch_hour_max_amps(vue, ch["channel_obj"], hour_start, hour_end)
            print(f"  {ch['name']:<25} max_amps={max_amps:.2f}")
        except Exception as exc:
            print(f"  {ch['name']:<25} ERROR: {exc}")

    device_gid = channels[0]["device_gid"] if channels else None
    if device_gid is not None:
        main_amps = emporia_client.fetch_main_hour_max_amps(vue, device_gid, hour_start, hour_end)
        print(f"\nCombined Main (mains legs summed) hourly max: {main_amps:.2f} A")

    print("\nLive reading:")
    device_gids = list({ch["device_gid"] for ch in channels})
    live = emporia_client.fetch_live_amps(vue, device_gids)
    for cid, amps in list(live.items())[:8]:
        print(f"  {cid:<20} {amps:.2f} A")
    if device_gid is not None:
        main_live = emporia_client.compute_main_from_readings(live, device_gid)
        print(f"  Main (live, legs summed): {main_live}")


if __name__ == "__main__":
    main()
