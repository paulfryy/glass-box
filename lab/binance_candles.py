"""Read finalized USD-M futures candles from Binance's public REST API."""

import argparse
import math

import pandas as pd
import requests


URL = "https://fapi.binance.com/fapi/v1/klines"
DAY = pd.Timedelta(days=1)


class FeedUnavailable(RuntimeError):
    """The direct API could not provide a usable response."""


def daily_candle(symbol, day, *, now=None):
    """Return (close, USDT volume) for one completed UTC day or None if absent."""
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    if day.tzinfo is None or day.utcoffset().total_seconds() != 0 or day != day.normalize():
        raise ValueError("Expected a UTC midnight for the candle open")
    next_day = day + DAY
    if now < next_day:
        raise ValueError("The requested daily candle is still open")
    start_ms = day.value // 1_000_000
    end_ms = next_day.value // 1_000_000 - 1
    params = {"symbol": symbol, "interval": "1d", "startTime": start_ms, "endTime": end_ms, "limit": 1}
    try:
        response = requests.get(URL, params=params, timeout=(4, 8))
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise FeedUnavailable(f"HTTP {exc.response.status_code}") from exc
    except requests.RequestException as exc:
        raise FeedUnavailable(type(exc).__name__) from exc

    try:
        rows = response.json()
        if rows == []:
            return None
        if not isinstance(rows, list) or len(rows) != 1:
            raise ValueError("expected a single candle")
        candle = rows[0]
        if candle[0] != start_ms or candle[6] != end_ms:
            raise ValueError("returned candle has unexpected boundaries")
        close, volume = float(candle[4]), float(candle[7])
        if not (math.isfinite(close) and math.isfinite(volume) and close > 0 and volume >= 0):
            raise ValueError("returned candle has invalid price or volume")
    except (ValueError, TypeError, IndexError, KeyError) as exc:
        raise FeedUnavailable(f"invalid candle response: {exc}") from exc
    return close, volume


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol", nargs="?", default="BTCUSDT")
    parser.add_argument("--day", help="UTC candle open date (default is the latest completed UTC day)")
    args = parser.parse_args()
    now = pd.Timestamp.now(tz="UTC")
    try:
        day = pd.Timestamp(args.day, tz="UTC") if args.day else now.normalize() - DAY
        candle = daily_candle(args.symbol.upper(), day, now=now)
    except (FeedUnavailable, ValueError) as exc:
        parser.exit(1, f"Binance futures candle probe failed: {exc}\n")
    if candle is None:
        parser.exit(1, f"Binance returned no candle for {args.symbol.upper()} on {day:%Y-%m-%d}\n")
    print(f"{args.symbol.upper()} {day:%Y-%m-%d} UTC close={candle[0]} quote_volume={candle[1]}")


if __name__ == "__main__":
    main()
