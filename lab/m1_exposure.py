"""Forward paper comparison of three exposure rules on the published M1 book."""

import pandas as pd


START = "2026-11-23"
WINDOW = 28
TARGET = 0.20
FIXED = 0.50
ACCOUNTS = ("m1_full", "m1_half", "m1_volatility")
FOLDER = "m1-exposure"
LOG = f"{FOLDER}/scales.csv"


def scale_for(week, gross):
    """Use the 28 daily closes through Sunday. A missing close means zero capped exposure."""
    dates = pd.date_range(end=week - pd.Timedelta(days=1), periods=WINDOW)
    window = gross.reindex(dates)
    if window.isna().any():
        return None, 0.0
    annualized = float(window.std() * 365**0.5)
    return annualized, min(1.0, TARGET / annualized) if annualized else 1.0


def weekly_scales(weeks, close_dates, scales, gross, now):
    """Record decisions during the publication window following each Monday close."""
    start = pd.Timestamp(START, tz="UTC")
    published = set(scales["week"])
    decisions = []
    for label in weeks:
        week = pd.Timestamp(label, tz="UTC")
        if label in published or week < start or week not in close_dates:
            continue
        if not week + pd.Timedelta(days=1) <= now < week + pd.Timedelta(days=3):
            continue
        volatility, scale = scale_for(week, gross)
        decisions.append({"week": label,
                          "annualized_volatility": None if volatility is None else round(volatility, 8),
                          "scale": round(scale, 8), "published_utc": now.strftime("%Y-%m-%d %H:%M")})
    return decisions


def exposure_by_date(dates, scales):
    mondays = (dates - pd.to_timedelta(dates.weekday, unit="D")).strftime("%Y-%m-%d")
    capped = pd.Series(scales.set_index("week")["scale"].reindex(mondays).to_numpy(), index=dates, dtype=float)
    active = capped.notna().astype(float)
    return dict(zip(ACCOUNTS, (active, active * FIXED, capped.fillna(0.0))))
