"""Observe when Binance publishes the daily candles needed by M1."""

import argparse
import csv
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, time, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from statistics import median
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


FIRST = date(2026, 11, 19)
LAST = date(2026, 12, 2)
ET = ZoneInfo("America/New_York")
ARCHIVE = "https://data.binance.vision/data/futures/um/daily/klines"


def cohort(closes, day):
    """Use contracts with a price on the most recent saved close before day."""
    with closes.open(encoding="utf-8", newline="") as handle:
        rows = csv.DictReader(handle)
        latest = None
        for row in rows:
            candle_day = date.fromisoformat(row.pop("time")[:10])
            if candle_day < day:
                latest = candle_day, row
    if latest is None:
        raise ValueError(f"No saved prices before {day}")
    reference, prices = latest
    symbols = sorted(symbol for symbol, value in prices.items() if value.strip().lower() not in {"", "nan"})
    if not symbols:
        raise ValueError(f"No active contracts on {reference}")
    return reference.isoformat(), symbols


def probe(day, symbol):
    path = f"{ARCHIVE}/{symbol}/1d/{symbol}-1d-{day}.zip"
    try:
        with urlopen(Request(path, method="HEAD"), timeout=10) as response:
            if response.status != 200 or not (response.headers.get("Content-Length") or "").isdigit():
                return symbol, None, f"unexpected HTTP {response.status} response"
            if int(response.headers["Content-Length"]) <= 0:
                return symbol, None, "empty archive object"
            header = response.headers.get("Last-Modified")
            try:
                modified = parsedate_to_datetime(header).astimezone(UTC).isoformat() if header else None
            except (TypeError, ValueError):
                modified = None
            return symbol, {"first_seen_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                            "last_modified_utc": modified}, None
    except HTTPError as exc:
        return symbol, None, None if exc.code == 404 else f"HTTP {exc.code}"
    except OSError as exc:
        return symbol, None, type(exc).__name__


def observe(directory, closes, day, now):
    path = directory / f"{day}.json"
    if path.exists():
        record = json.loads(path.read_text(encoding="utf-8"))
    else:
        reference, symbols = cohort(closes, day)
        record = {"candle_day": day.isoformat(), "reference_day": reference, "required": symbols, "files": {}}
    pending = sorted(set(record["required"]) - record["files"].keys())
    if not pending:
        print(f"{day}: all {len(record['required'])} candles already observed")
        return

    with ThreadPoolExecutor(max_workers=8) as pool:
        observations = list(pool.map(lambda symbol: probe(day, symbol), pending))
    record["last_poll_utc"] = now.isoformat(timespec="seconds")
    record["errors"] = {symbol: error for symbol, _, error in observations if error}
    record["saw_missing"] = record.get("saw_missing", False) or any(
        file is None and error is None for _, file, error in observations
    )
    record["files"].update({symbol: file for symbol, file, _ in observations if file})
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{day}: {len(record['files'])}/{len(record['required'])} candles visible, {len(record['errors'])} request errors")


def local_time(stamp):
    return stamp.astimezone(ET).strftime("%b %d, %I:%M %p").replace(", 0", ", ")


def summary(root):
    records = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((root / "days").glob("*.json"))]
    lines = ["# Binance archive timing", "", f"UTC candle days **{FIRST} through {LAST}**.", "",
             "The Cloudflare timer dispatches checks every 10 minutes from 6 a.m. through 4:50 p.m. UTC, "
             "with catch-up checks in the 10 p.m. UTC hour. "
             "Each day tracks contracts with a price on the latest saved M1 close preceding it.", "",
             "Times below are Eastern. First seen means the first successful poll. The server timestamp is "
             "`Last-Modified`, which can also reflect a later correction.", "",
             "| Candle day (UTC) | Seen | First file seen | Full set seen | Latest server timestamp |",
             "|---|---:|---|---|---|"]
    lags, mondays = [], []
    complete_days = already_present = 0
    for r in records:
        day = date.fromisoformat(r["candle_day"])
        files = r["files"].values()
        observed = [datetime.fromisoformat(f["first_seen_utc"]) for f in files]
        modified = [datetime.fromisoformat(f["last_modified_utc"]) for f in files if f["last_modified_utc"]]
        complete = len(observed) == len(r["required"])
        ready = "—"
        if complete:
            complete_days += 1
            ready = local_time(max(observed))
        if complete and r.get("saw_missing", False):
            close = datetime.combine(day + timedelta(days=1), time(), UTC)
            lag = (max(observed) - close).total_seconds() / 3600
            lags.append(lag)
            if day.weekday() == 0:
                mondays.append(lag)
        elif complete:
            already_present += 1
            ready += " (already visible)"
        lines.append(f"| {day} | {len(observed)}/{len(r['required'])} | "
                     f"{local_time(min(observed)) if observed else '—'} | "
                     f"{ready} | "
                     f"{local_time(max(modified)) if modified else '—'} |")
    lines += ["", f"**{complete_days} of {(LAST - FIRST).days + 1}** days have a complete observed set."]
    if already_present:
        lines += [f"{already_present} set(s) were already visible when checked. They provide upper bounds and are "
                  "excluded from the timing median."]
    if lags:
        lines += [f"For {len(lags)} day(s) observed going from incomplete to complete, the median was "
                  f"**{median(lags):.1f} hours** after close "
                  f"(range {min(lags):.1f}–{max(lags):.1f} hours)."]
    if mondays:
        lines += [f"For the {len(mondays)} observed Monday candle day(s), the median was **{median(mondays):.1f} hours**."]
    lines += ["", "[Daily observation records](days/)", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--closes", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    now = datetime.now(UTC)
    day = now.date() - timedelta(days=1)
    done = now >= datetime.combine(LAST + timedelta(days=1), time(22), UTC)
    if FIRST <= day <= LAST:
        observe(args.output / "days", args.closes, day, now)
    if FIRST <= day:
        args.output.mkdir(parents=True, exist_ok=True)
        report = summary(args.output)
        (args.output / "README.md").write_text(report, encoding="utf-8")
        if path := os.environ.get("GITHUB_STEP_SUMMARY"):
            Path(path).write_text(report, encoding="utf-8")
    if path := os.environ.get("GITHUB_OUTPUT"):
        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write(f"finished={'true' if done else 'false'}\n")


if __name__ == "__main__":
    main()
