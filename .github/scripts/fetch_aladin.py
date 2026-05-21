#!/usr/bin/env python3
"""
CHMI ALADIN-CZ forecast pre-processor.

Downloads the latest 5 GRIB2 files from opendata.chmi.cz, extracts values at
every active CHMI station coordinate, and writes chmi_forecast.json.

Dependencies: pip install eccodes numpy scipy requests
"""

import bz2
import json
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import eccodes
import numpy as np
import requests
from scipy.spatial import cKDTree

STATION_LIST_URL = (
    "https://opendata.chmi.cz/meteorology/weather/recent/data/metadata_sta.json"
)

BASE_URL = "https://opendata.chmi.cz/meteorology/weather/nwp_aladin/CZ_1km"

# GRIB2 filename fragments for each variable
VARIABLES = {
    "temp":     "CLSTEMPERATURE",
    "humidity": "CLSHUMI_RELATIVE",
    "pressure": "MSLPRESSURE",
    "wind":     "CLSWIND_SPEED",
    "solar":    "SURFRF_SHORT_DO",
}

RUN_HOURS = [0, 6, 12, 18]


def latest_run(now: datetime) -> tuple[datetime, int]:
    """Return (run_dt, run_hour) for the most recent run actually present on the server.

    Probes with HEAD requests rather than calculating expected availability time —
    observed CHMI publication lag is 3–5 h, not the 2 h assumed in the plan.
    Checks up to the last 48 h of candidate runs (8 runs × 6 h apart).
    """
    for delta_hours in range(0, 48, 6):
        candidate = now - timedelta(hours=delta_hours)
        for rh in sorted(RUN_HOURS, reverse=True):
            run_dt = candidate.replace(hour=rh, minute=0, second=0, microsecond=0)
            if run_dt > now:
                continue
            probe_url = build_url(run_dt, "CLSTEMPERATURE")
            try:
                r = requests.head(probe_url, timeout=10, allow_redirects=True)
                if r.status_code == 200:
                    print(f"  Found run: {run_dt.strftime('%Y-%m-%dT%H:%M:%SZ')} (HTTP 200)", flush=True)
                    return run_dt, rh
            except requests.RequestException:
                continue
    raise RuntimeError("No ALADIN run found in last 48 h")


def build_url(run_dt: datetime, var_fragment: str) -> str:
    rh = run_dt.hour
    date_str = run_dt.strftime("%Y%m%d")
    fname = f"ALADCZ1K4opendata_{date_str}{rh:02d}_{var_fragment}.grb.bz2"
    return f"{BASE_URL}/{rh:02d}/{fname}"


def download_and_decompress(url: str) -> bytes:
    print(f"  GET {url}", flush=True)
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    return bz2.decompress(r.content)


def read_grib_messages(grib_bytes: bytes) -> list[dict]:
    """Return list of {step_hours, values_flat, lats, lons} per GRIB message."""
    messages = []
    with tempfile.NamedTemporaryFile(suffix=".grb") as f:
        f.write(grib_bytes)
        f.flush()
        with open(f.name, "rb") as fh:
            while True:
                msg = eccodes.codes_grib_new_from_file(fh)
                if msg is None:
                    break
                try:
                    step = int(eccodes.codes_get(msg, "endStep"))
                    values = eccodes.codes_get_values(msg)
                    lats = eccodes.codes_get_array(msg, "latitudes")
                    lons = eccodes.codes_get_array(msg, "longitudes")
                    messages.append({
                        "step_hours": step,
                        "values": values,
                        "lats": lats,
                        "lons": lons,
                    })
                finally:
                    eccodes.codes_release(msg)
    return messages


def fetch_station_list() -> list[dict]:
    """Return [{id, lat, lon}, ...] for all active CHMI stations."""
    print("  Fetching CHMI station metadata …", flush=True)
    r = requests.get(STATION_LIST_URL, timeout=30)
    r.raise_for_status()
    raw = r.json()

    stations = []
    for entry in raw.get("data", {}).get("stations", []):
        wsi = entry.get("wsi") or entry.get("id")
        lat = entry.get("lat")
        lon = entry.get("lon")
        if wsi and lat is not None and lon is not None:
            stations.append({"id": str(wsi), "lat": float(lat), "lon": float(lon)})
    return stations


def build_output(run_dt: datetime, all_messages: dict[str, list[dict]], stations: list[dict]) -> dict:
    """Build the per-station JSON output."""
    # All variables must have the same grid — use temp as reference
    ref_msgs = all_messages["temp"]
    if not ref_msgs:
        raise RuntimeError("No messages found in temperature GRIB file")

    # Build KD-tree from the first message's grid (same for all messages)
    lats_grid = ref_msgs[0]["lats"]
    lons_grid = ref_msgs[0]["lons"]
    coords_rad = np.deg2rad(np.column_stack([lats_grid, lons_grid]))
    tree = cKDTree(coords_rad)

    # Sort messages by step_hours
    for var in all_messages:
        all_messages[var].sort(key=lambda m: m["step_hours"])

    # Steps present in all variables (intersection)
    step_sets = [set(m["step_hours"] for m in msgs) for msgs in all_messages.values()]
    common_steps = sorted(step_sets[0].intersection(*step_sets[1:]))

    # Map step → message index per variable
    step_to_msg: dict[str, dict[int, dict]] = {}
    for var, msgs in all_messages.items():
        step_to_msg[var] = {m["step_hours"]: m for m in msgs}

    # Pre-query nearest grid index for each station
    station_idx = {}
    station_coords = np.deg2rad([[s["lat"], s["lon"]] for s in stations])
    _, indices = tree.query(station_coords)
    for i, s in enumerate(stations):
        station_idx[s["id"]] = indices[i]

    # Build time strings
    times = [
        (run_dt + timedelta(hours=step)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for step in common_steps
    ]

    output_stations = {}
    for s in stations:
        sid = s["id"]
        idx = station_idx[sid]
        station_entry = {"times": times}
        for var in VARIABLES:
            station_entry[var] = [
                round(float(step_to_msg[var][step]["values"][idx]), 2)
                for step in common_steps
            ]
        output_stations[sid] = station_entry

    valid_until = (run_dt + timedelta(hours=max(common_steps))).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model_run":    run_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "valid_until":  valid_until,
        "stations":     output_stations,
    }


def main() -> None:
    now = datetime.now(timezone.utc)
    run_dt, _ = latest_run(now)
    print(f"Using ALADIN run: {run_dt.strftime('%Y-%m-%dT%H:%M:%SZ')}", flush=True)

    all_messages: dict[str, list[dict]] = {}
    for var_key, var_fragment in VARIABLES.items():
        url = build_url(run_dt, var_fragment)
        print(f"Downloading {var_key} …", flush=True)
        grib_bytes = download_and_decompress(url)
        print(f"  Parsing GRIB2 ({len(grib_bytes)//1024} KB uncompressed) …", flush=True)
        all_messages[var_key] = read_grib_messages(grib_bytes)
        print(f"  {len(all_messages[var_key])} messages", flush=True)

    stations = fetch_station_list()
    print(f"Stations: {len(stations)}", flush=True)

    print("Building output …", flush=True)
    output = build_output(run_dt, all_messages, stations)

    out_path = Path("chmi_forecast.json")
    out_path.write_text(json.dumps(output, separators=(",", ":")), encoding="utf-8")
    size_kb = out_path.stat().st_size // 1024
    print(f"Written {out_path} ({size_kb} KB, {len(output['stations'])} stations)", flush=True)


if __name__ == "__main__":
    main()
