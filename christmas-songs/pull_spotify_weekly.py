"""
Pull weekly Spotify charts (top 200) for every region on the charts.spotify.com
dropdown, for 1 October to the end of February.

By default it pulls the 2025-26 season only. Use --first-season to go further
back, for example --first-season 2017.

It needs the same browser token as pull_spotify_charts.py:

  1. Log in at https://charts.spotify.com and open any chart.
  2. Open DevTools (F12) > Network, refresh the page and click a request to
     charts-spotify-com-service.spotify.com.
  3. Copy the value of the 'authorization' request header after 'Bearer '.
  4. In Git Bash:
       export SPOTIFY_CHARTS_TOKEN="<token>"
       python pull_spotify_weekly.py
     In PowerShell:
       $env:SPOTIFY_CHARTS_TOKEN = "<token>"
       python pull_spotify_weekly.py

Tokens last about an hour. Each week is saved as its own CSV under
data/raw_weekly/<region>, so if the token expires just grab a new one and run
again. Weeks already downloaded are skipped. When everything's in, all the
regions are combined into data/spotify_weekly_all_regions.csv.
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

API = "https://charts-spotify-com-service.spotify.com/auth/v0/charts/regional-{region}-weekly/{day}"
HERE = Path(__file__).resolve().parent
RAW_DIR = HERE / "data" / "raw_weekly"
OUT_FILE = HERE / "data" / "spotify_weekly_all_regions.csv"
NO_CHART = RAW_DIR / "_no_chart.txt"     # regions with no weekly chart, so later runs skip them

# The regions on the charts.spotify.com dropdown. A region with no weekly chart is skipped
# (and remembered in NO_CHART), so an extra code here costs one request, not a failed run.
REGIONS = {
    "global": "Global", "ar": "Argentina", "au": "Australia", "at": "Austria", "by": "Belarus",
    "be": "Belgium", "bo": "Bolivia", "br": "Brazil", "bg": "Bulgaria", "ca": "Canada",
    "cl": "Chile", "co": "Colombia", "cr": "Costa Rica", "cy": "Cyprus", "cz": "Czech Republic",
    "dk": "Denmark", "do": "Dominican Republic", "ec": "Ecuador", "eg": "Egypt",
    "sv": "El Salvador", "ee": "Estonia", "fi": "Finland", "fr": "France", "de": "Germany",
    "gr": "Greece", "gt": "Guatemala", "hn": "Honduras", "hk": "Hong Kong", "hu": "Hungary",
    "is": "Iceland", "in": "India", "id": "Indonesia", "ie": "Ireland", "il": "Israel",
    "it": "Italy", "jp": "Japan", "kz": "Kazakhstan", "lv": "Latvia", "lt": "Lithuania",
    "lu": "Luxembourg", "my": "Malaysia", "mx": "Mexico", "ma": "Morocco", "nl": "Netherlands",
    "nz": "New Zealand", "ni": "Nicaragua", "ng": "Nigeria", "no": "Norway", "pk": "Pakistan",
    "pa": "Panama", "py": "Paraguay", "pe": "Peru", "ph": "Philippines", "pl": "Poland",
    "pt": "Portugal", "ro": "Romania", "sa": "Saudi Arabia", "sg": "Singapore", "sk": "Slovakia",
    "za": "South Africa", "kr": "South Korea", "es": "Spain", "se": "Sweden",
    "ch": "Switzerland", "tw": "Taiwan", "th": "Thailand", "tr": "Turkey",
    "ae": "United Arab Emirates", "ua": "Ukraine", "gb": "United Kingdom", "uy": "Uruguay",
    "us": "United States", "ve": "Venezuela", "vn": "Vietnam",
}

COLUMNS = [
    "week_end", "region", "region_name", "rank", "previous_rank", "peak_rank", "weeks_on_chart",
    "streams", "track_name", "artists", "track_uri", "labels", "release_date",
]

# Weekly charts run Friday to Thursday and are dated by the Thursday. If Spotify ever changes
# that, find_chart_weekday() works out the new day before anything else is fetched.
THURSDAY = 3


def season_weeks(first, last, weekday=THURSDAY):
    """Every chart date from 1 Oct to the last day of Feb, for each season, up to yesterday."""
    yesterday = date.today() - timedelta(days=1)
    for year in range(first, last + 1):
        day = date(year, 10, 1)
        day += timedelta(days=(weekday - day.weekday()) % 7)
        end = min(date(year + 1, 2, calendar.monthrange(year + 1, 2)[1]), yesterday)
        while day <= end:
            yield day
            day += timedelta(days=7)


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
    """Fetch one week, waiting and retrying when Spotify says we're going too fast.
    Returns None when there's no chart for that week."""
    wait = 30
    for attempt in range(1, attempts + 1):
        try:
            return fetch(region, day, token)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                sys.exit(f"\nToken rejected ({e.code}). Copy a fresh one from charts.spotify.com and run again.")
            if e.code in (400, 404):
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
    sys.exit(f"\nStill blocked after {attempts} attempts. Leave it for half an hour, then run again. Saved weeks are kept.")


def find_chart_weekday(token):
    """Check which weekday the weekly charts are dated by, using the global chart for the week
    of Christmas 2025. Thursday is tried first, so this is normally one request."""
    christmas_week = date(2025, 12, 25)                  # a Thursday
    for offset in (0, 1, -1, 2, -2, 3, -3):
        day = christmas_week + timedelta(days=offset)
        if fetch_with_retry("global", day, token) is not None:
            return day.weekday()
    sys.exit("\nCouldn't find a weekly global chart around Christmas 2025. Check the API address.")


def to_rows(payload, region, day):
    rows = []
    for entry in payload.get("entries", []):
        chart = entry.get("chartEntryData", {})
        track = entry.get("trackMetadata", {})
        rows.append({
            "week_end": day.isoformat(),
            "region": region,
            "region_name": REGIONS.get(region, region),
            "rank": chart.get("currentRank"),
            "previous_rank": chart.get("previousRank"),
            "peak_rank": chart.get("peakRank"),
            "weeks_on_chart": chart.get("appearancesOnChart"),
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


def regions_without_chart():
    return set(NO_CHART.read_text().split()) if NO_CHART.exists() else set()


def pull_region(region, weeks, token, pause):
    """Download any missing weeks for one region."""
    folder = RAW_DIR / region
    missing = [d for d in weeks if not (folder / f"{d}.csv").exists()]
    if not missing:
        print(f"[{region}] all {len(weeks)} weeks already saved")
        return

    # a region we've never fetched: check the newest week first, so a region with no weekly
    # chart costs one request rather than one for every week
    if not folder.exists():
        latest = weeks[-1]
        payload = fetch_with_retry(region, latest, token)
        if payload is None:
            print(f"[{region}] no weekly chart, skipping this region from now on")
            RAW_DIR.mkdir(parents=True, exist_ok=True)
            with open(NO_CHART, "a") as f:
                f.write(region + "\n")
            return
        write_csv(folder / f"{latest}.csv", to_rows(payload, region, latest))
        missing.remove(latest)
        time.sleep(pause)

    print(f"[{region}] {len(weeks) - len(missing)} of {len(weeks)} weeks already saved, {len(missing)} to fetch")
    for i, day in enumerate(missing, 1):
        payload = fetch_with_retry(region, day, token)
        if payload is None:
            print(f"[{region}] {day} no chart published, skipping")
        else:
            rows = to_rows(payload, region, day)
            write_csv(folder / f"{day}.csv", rows)
            print(f"[{region}] {i}/{len(missing)} {day} {len(rows)} tracks")
        time.sleep(pause)


def combine():
    files = sorted(RAW_DIR.glob("*/*.csv"))
    with open(OUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for path in files:
            with open(path, newline="", encoding="utf-8") as week_file:
                writer.writerows(csv.DictReader(week_file))
    regions = len({p.parent.name for p in files})
    print(f"combined {len(files)} weeks from {regions} regions into {OUT_FILE}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--first-season", type=int, default=2025,
                        help="October year of the first season, for example 2017 (default: 2025)")
    parser.add_argument("--last-season", type=int, default=2025,
                        help="October year of the last season (default: 2025)")
    parser.add_argument("--region", nargs="+", default=list(REGIONS),
                        help="'global' or two-letter country codes, for example gb us (default: every region)")
    parser.add_argument("--pause", type=float, default=2.0, help="seconds between requests (default: 2)")
    parser.add_argument("--combine-only", action="store_true", help="skip downloading, just rebuild the combined CSV")
    args = parser.parse_args()

    if not args.combine_only:
        token = os.environ.get("SPOTIFY_CHARTS_TOKEN") or input("Paste your charts.spotify.com bearer token: ").strip()
        token = token.removeprefix("Bearer ").strip()

        weekday = find_chart_weekday(token)
        if weekday != THURSDAY:
            print(f"Weekly charts are dated by {calendar.day_name[weekday]}, using that")
        weeks = list(season_weeks(args.first_season, args.last_season, weekday))

        skip = regions_without_chart()
        regions = [r.lower() for r in args.region if r.lower() not in skip]
        print(f"{len(regions)} regions, {len(weeks)} weeks each, from {weeks[0]} to {weeks[-1]}")
        for n, region in enumerate(regions, 1):
            print(f"\n== {n}/{len(regions)} {REGIONS.get(region, region)} ==")
            pull_region(region, weeks, token, args.pause)
    combine()


if __name__ == "__main__":
    main()
