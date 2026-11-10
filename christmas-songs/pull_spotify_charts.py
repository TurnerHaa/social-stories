"""
Pull daily Spotify charts (top 200) for 1 October to the end of February,
for the seasons 2020-21 through 2025-26.

charts.spotify.com needs a logged-in session, so the script uses a token
copied from your browser:

  1. Log in at https://charts.spotify.com and open any daily chart.
  2. Open DevTools (F12) > Network, refresh the page and click a request to
     charts-spotify-com-service.spotify.com.
  3. Copy the value of the 'authorization' request header after 'Bearer '.
  4. Run:
       $env:SPOTIFY_CHARTS_TOKEN = "<token>"
       python pull_spotify_charts.py

Tokens last about an hour. Each day is saved as its own CSV under data/raw,
so if the token expires just grab a new one and run again. Days already
downloaded are skipped. When everything's in, the days are combined into
data/spotify_daily_<region>.csv.
"""

import argparse
import calendar
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

API = "https://charts-spotify-com-service.spotify.com/auth/v0/charts/regional-{region}-daily/{day}"
FIRST_SEASON, LAST_SEASON = 2017, 2025  # season 2025 runs 1 Oct 2025 to 28 Feb 2026
HERE = Path(__file__).resolve().parent
RAW_DIR = HERE / "data" / "raw"
OUT_DIR = HERE / "data"

COLUMNS = [
    "date", "region", "rank", "previous_rank", "peak_rank", "days_on_chart",
    "streams", "track_name", "artists", "track_uri", "labels", "release_date",
]


def season_days(first=FIRST_SEASON, last=LAST_SEASON):
    """Every date from 1 Oct to the last day of Feb, for each season."""
    for year in range(first, last + 1):
        day = date(year, 10, 1)
        end = date(year + 1, 2, calendar.monthrange(year + 1, 2)[1])
        while day <= end:
            yield day
            day += timedelta(days=1)


def fetch(region, day, token):
    req = urllib.request.Request(
        API.format(region=region, day=day.isoformat()),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def fetch_with_retry(region, day, token, attempts=8):
    """Fetch one day, waiting and retrying when Spotify says we're going too fast.
    Returns None when there's no chart for that day."""
    wait = 30
    for attempt in range(1, attempts + 1):
        try:
            return fetch(region, day, token)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                sys.exit(f"\nToken rejected ({e.code}). Copy a fresh one from charts.spotify.com and run again.")
            if e.code == 404:
                return None
            if e.code != 429 and e.code < 500:
                raise
            retry_after = e.headers.get("Retry-After", "")
            delay = int(retry_after) if retry_after.isdigit() else wait
            reason = "rate limited" if e.code == 429 else f"server error {e.code}"
        except (urllib.error.URLError, TimeoutError) as e:
            delay, reason = wait, f"connection problem ({e})"
        print(f"[{region}] {day} {reason}, waiting {delay}s (attempt {attempt} of {attempts})")
        time.sleep(delay)
        wait = min(wait * 2, 600)
    sys.exit(f"\nStill blocked after {attempts} attempts. Leave it for half an hour, then run again. Saved days are kept.")


def to_rows(payload, region, day):
    rows = []
    for entry in payload.get("entries", []):
        chart = entry.get("chartEntryData", {})
        track = entry.get("trackMetadata", {})
        rows.append({
            "date": day.isoformat(),
            "region": region,
            "rank": chart.get("currentRank"),
            "previous_rank": chart.get("previousRank"),
            "peak_rank": chart.get("peakRank"),
            "days_on_chart": chart.get("appearancesOnChart"),
            "streams": (chart.get("rankingMetric") or {}).get("value"),
            "track_name": track.get("trackName"),
            "artists": ", ".join(a.get("name", "") for a in track.get("artists", [])),
            "track_uri": track.get("trackUri"),
            "labels": ", ".join(l.get("name", "") for l in track.get("labels", [])),
            "release_date": track.get("releaseDate"),
        })
    return rows


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def pull_region(region, token, pause):
    """Download any missing days. Returns True when every day is present."""
    days = list(season_days())
    missing = [d for d in days if not (RAW_DIR / region / f"{d}.csv").exists()]
    print(f"[{region}] {len(days) - len(missing)} of {len(days)} days already saved, {len(missing)} to fetch")

    for i, day in enumerate(missing, 1):
        payload = fetch_with_retry(region, day, token)
        if payload is None:
            print(f"[{region}] {day} no chart published, skipping")
            continue
        rows = to_rows(payload, region, day)
        write_csv(RAW_DIR / region / f"{day}.csv", rows)
        print(f"[{region}] {i}/{len(missing)} {day} {len(rows)} tracks")
        time.sleep(pause)

    return all((RAW_DIR / region / f"{d}.csv").exists() for d in days)


def combine(region):
    files = sorted((RAW_DIR / region).glob("*.csv"))
    out = OUT_DIR / f"spotify_daily_{region}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for path in files:
            with open(path, newline="", encoding="utf-8") as day_file:
                writer.writerows(csv.DictReader(day_file))
    print(f"[{region}] combined {len(files)} days into {out}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--region", nargs="+", default=["global"],
                        help="'global' or two-letter country codes, for example gb us (default: global)")
    parser.add_argument("--pause", type=float, default=2.0, help="seconds between requests (default: 2)")
    parser.add_argument("--combine-only", action="store_true", help="skip downloading, just rebuild the combined CSVs")
    args = parser.parse_args()

    regions = [r.lower() for r in args.region]
    if not args.combine_only:
        token = os.environ.get("SPOTIFY_CHARTS_TOKEN") or input("Paste your charts.spotify.com bearer token: ").strip()
        token = token.removeprefix("Bearer ").strip()
        for region in regions:
            pull_region(region, token, args.pause)
    for region in regions:
        combine(region)


if __name__ == "__main__":
    main()
