"""Compare the same weekly M1 trade quantities at the close and after publication."""

import argparse
import csv
import io
import json
import shutil
import subprocess
import tempfile
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.request import urlopen

import m1_exposure


LAB = Path(__file__).resolve().parent
FOLDER = "m1-execution"
ARCHIVE = "https://data.binance.vision/data/futures/um/daily/aggTrades"
NOTIONAL = 5000
WINDOW = timedelta(hours=1)


def read_books(source):
    books = defaultdict(dict)
    for row in csv.DictReader(source):
        books[row["week"]][row["symbol"]] = float(row["weight"])
    return books


def weekly_orders(books, week, close):
    """Freeze signed token quantities using the Monday reference prices."""
    older = max((key for key in books if key < week), default=None)
    prior, target = books.get(older, {}), books[week]
    orders = {}
    for symbol in sorted(prior.keys() | target.keys()):
        change = target.get(symbol, 0) - prior.get(symbol, 0)
        if abs(change) < 0.0000005:
            continue
        reference = float(close[symbol]) if close.get(symbol) else None
        orders[symbol] = {"weight_change": round(change, 6), "reference_close": reference,
                          "quantity": NOTIONAL * round(change, 6) / reference if reference else None}
    return orders


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def capture(lab, commit, observed_at):
    # When a commit contains new positions, its successful push provides the lower bound for the fill search.
    parent = subprocess.check_output(["git", "show", f"{commit}^:lab/positions.csv"], cwd=lab.parent, text=True)
    before = read_books(io.StringIO(parent))
    with (lab / "positions.csv").open(encoding="utf-8", newline="") as handle:
        after = read_books(handle)
    for week in sorted(after.keys() - before.keys()):
        if week < m1_exposure.START:
            continue
        path = lab / FOLDER / "publications" / f"{week}.json"
        if path.exists():
            continue
        with (lab / "data/closes.csv").open(encoding="utf-8", newline="") as handle:
            close = next((row for row in csv.DictReader(handle) if row["time"][:10] == week), {})
        save(path, {"week": week, "commit": commit, "public_by_utc": observed_at.isoformat(timespec="milliseconds"),
                    "orders": weekly_orders(after, week, close)})


def first_trade(rows, start_ms, stop_ms):
    earliest = None
    for row in rows:
        if not row or not row[0].isdigit():
            continue
        stamp = int(row[5])
        if start_ms <= stamp < stop_ms and (earliest is None or stamp < earliest[0]):
            earliest = stamp, float(row[1]), row[0]
    return earliest


def archived_trade(symbol, day, start_ms, stop_ms):
    url = f"{ARCHIVE}/{symbol}/{symbol}-aggTrades-{day}.zip"
    with tempfile.TemporaryFile() as buffer, urlopen(url, timeout=20) as response:
        shutil.copyfileobj(response, buffer)
        with zipfile.ZipFile(buffer) as archive, archive.open(archive.namelist()[0]) as member:
            return first_trade(csv.reader(io.TextIOWrapper(member, encoding="utf-8")), start_ms, stop_ms)


def match_order(symbol, order, start, now):
    if order["quantity"] is None:
        return {"state": "no_reference_close"}
    end = start + WINDOW
    if now.date() <= (end - timedelta(milliseconds=1)).date():
        return {"state": "waiting_for_archive"}
    # Round up so the comparison never uses a millisecond before the publication observation.
    start_ms = (int(start.timestamp() * 1_000_000) + 999) // 1000
    end_ms = start_ms + int(WINDOW.total_seconds() * 1000)
    day = start.date()
    while day <= (end - timedelta(milliseconds=1)).date():
        try:
            trade = archived_trade(symbol, day, start_ms, end_ms)
        except (OSError, zipfile.BadZipFile, ValueError, IndexError) as exc:
            print(f"Execution archive {symbol} {day} pending: {exc}")
            return {"state": "waiting_for_archive"}
        if trade:
            stamp, price, trade_id = trade
            return {"state": "matched", "trade_id": trade_id, "trade_price": price,
                    "trade_utc": datetime.fromtimestamp(stamp / 1000, UTC).isoformat(timespec="milliseconds"),
                    "delay_cost_usd": round(order["quantity"] * (price - order["reference_close"]), 6)}
        day += timedelta(days=1)
    return {"state": "no_trade_in_window"}


def reconcile(lab, now):
    for path in sorted((lab / FOLDER / "publications").glob("*.json")):
        publication = json.loads(path.read_text(encoding="utf-8"))
        out = lab / FOLDER / "comparisons" / path.name
        results = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        start = datetime.fromisoformat(publication["public_by_utc"])
        pending = [(s, order) for s, order in publication["orders"].items()
                   if s not in results or results[s]["state"] == "waiting_for_archive"]
        if not pending:
            continue
        with ThreadPoolExecutor(max_workers=4) as pool:
            matches = pool.map(lambda pair: match_order(*pair, start, now), pending)
            results.update({s: result for (s, _), result in zip(pending, matches)})
        save(out, results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-commit", help="Record picks after this commit has been pushed")
    args = parser.parse_args()
    instant = datetime.now(UTC)
    if args.capture_commit:
        capture(LAB, args.capture_commit, instant)
    else:
        reconcile(LAB, instant)
