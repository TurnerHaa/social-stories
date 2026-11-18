"""
Pull Spotify's weekly charts (top 200) for every week of 2025, for the global chart
and the English-speaking countries in each hemisphere.

It shares its downloads with pull_spotify_weekly.py (data/raw_weekly/<region>), so
weeks either script has already saved are skipped.

Run it with a token from charts.spotify.com:
  Git Bash:    export SPOTIFY_CHARTS_TOKEN="<token>"
  PowerShell:  $env:SPOTIFY_CHARTS_TOKEN = "<token>"
then:
  python pull_spotify_weekly_2025.py

That's 53 weeks for each of eight charts, fewer the weeks already saved, so it
takes 10 to 20 minutes. The weeks are combined into
data/spotify_weekly_english_2025.csv.
"""

import argparse
import csv
import os
from datetime import date, timedelta

from pull_spotify_weekly import COLUMNS, RAW_DIR, REGIONS, find_chart_weekday, pull_region

HERE = RAW_DIR.parent.parent

# Countries on the charts.spotify.com dropdown where English is the main language, split by
# hemisphere. Singapore, the Philippines, India, Nigeria and Pakistan have English as an
# official language but most people there speak another language day to day, so they're left out.
NORTH = ["us", "gb", "ca", "ie"]
SOUTH = ["au", "nz", "za"]
CHARTS = ["global"] + NORTH + SOUTH


def year_weeks(year, weekday):
    """Every chart week that covers part of the year: from the week ending in the first seven
    days of January to the last week that starts in December."""
    day = date(year, 1, 1)
    day += timedelta(days=(weekday - day.weekday()) % 7)
    last = min(date(year + 1, 1, 6), date.today() - timedelta(days=1))
    while day <= last:
        yield day
        day += timedelta(days=7)


def combine(regions, weeks, out):
    files = [RAW_DIR / r / f"{d}.csv" for r in regions for d in weeks]
    found = [f for f in files if f.exists()]
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS + ["hemisphere"])
        writer.writeheader()
        for path in found:
            region = path.parent.name
            hemisphere = "North" if region in NORTH else "South" if region in SOUTH else ""
            with open(path, newline="", encoding="utf-8") as week_file:
                for row in csv.DictReader(week_file):
                    row["region_name"] = REGIONS.get(region, region)
                    row["hemisphere"] = hemisphere
                    writer.writerow(row)
    print(f"combined {len(found)} of {len(files)} chart weeks into {out}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--year", type=int, default=2025, help="calendar year to pull (default: 2025)")
    parser.add_argument("--pause", type=float, default=2.0, help="seconds between requests (default: 2)")
    parser.add_argument("--combine-only", action="store_true", help="skip downloading, just rebuild the combined CSV")
    args = parser.parse_args()

    weekday = 3                                        # Thursday, unless Spotify says otherwise
    if not args.combine_only:
        token = os.environ.get("SPOTIFY_CHARTS_TOKEN") or input("Paste your charts.spotify.com bearer token: ").strip()
        token = token.removeprefix("Bearer ").strip()
        weekday = find_chart_weekday(token)

    weeks = list(year_weeks(args.year, weekday))
    if not args.combine_only:
        print(f"{len(CHARTS)} charts, {len(weeks)} weeks each, from {weeks[0]} to {weeks[-1]}")
        for n, region in enumerate(CHARTS, 1):
            print(f"\n== {n}/{len(CHARTS)} {REGIONS[region]} ==")
            pull_region(region, weeks, token, args.pause)
    combine(CHARTS, weeks, HERE / "data" / f"spotify_weekly_english_{args.year}.csv")


if __name__ == "__main__":
    main()
