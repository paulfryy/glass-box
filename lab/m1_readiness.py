"""Decide whether a new M1 week has enough data to publish."""

from pathlib import Path

import pandas as pd

import m1_exposure


FIRST = pd.Timestamp(m1_exposure.START, tz="UTC")
DAY = pd.Timedelta(days=1)
REPORT = "m1-readiness.json"


def check(prices, volumes, now, published, prior_symbols=()):
    week = now.normalize() - now.weekday() * DAY
    label = week.date().isoformat()
    recent = prices.reindex(index=[week - 2 * DAY, week - DAY])
    expected = sorted(set(recent.columns[recent.notna().any()]) | set(prior_symbols) | {"BTCUSDT"})
    # Include Saturday's active contracts so a missing Sunday file does not shrink the universe.
    days = [week - DAY, week]
    missing = {}
    for day in days:
        valid = prices.reindex(index=[day], columns=expected).notna() & volumes.reindex(index=[day], columns=expected).notna()
        missing[day.date().isoformat()] = sorted(valid.columns[~valid.iloc[0]])
    if label in published:
        state = "published"
    elif now < week + DAY:
        state = "awaiting_close"
    elif now >= week + 3 * DAY:
        state = "skipped"
    elif any(missing.values()):
        state = "waiting_for_candles"
    else:
        state = "ready"
    return {"week": label, "state": state, "expected": expected, "missing": missing}


def hold_unpublished(book, positions_path):
    """Carry the preceding book when a new week slipped past the publication gate."""
    if Path(positions_path).exists():
        weeks = set(pd.read_csv(positions_path, usecols=["week"])["week"])
    else:
        weeks = set()
    for week in pd.date_range(FIRST, book.index.max(), freq="W-MON"):
        if week.date().isoformat() in weeks:
            continue
        prior = book.loc[book.index < week]
        if prior.empty:
            continue
        span = (book.index >= week) & (book.index < week + 7 * DAY)
        book.loc[span] = prior.iloc[-1].to_numpy()
    return book
