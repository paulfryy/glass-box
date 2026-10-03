#!/usr/bin/env python3
"""
Forward test F2: S1-smooth72 (lab/forward_protocol.md, fixed 2026-10-03; rule from report 23).

Each Monday close: the 50 most liquid eligible perps of the frozen universe (lab/seesaw/universe.json),
excluding the five large coins. Every hour: OLS prediction of each listed coin's next-hour return from the
five large coins' latest hourly returns (720-hour window, refitted daily at 00:00 UTC); target long the top
20%, short the bottom 20%, each side half the account; HELD position = average of the last 72 hourly targets.
Costs 0.05% + 0.05% per unit traded; funding charged hourly at 1/24 of the day's total.

Positions are computed once a day from Binance's archive (published ~08:20 UTC the next day), using only data
before each hour. Each finished day's hourly positions are written to lab/seesaw/positions/<day>.csv and never
rewritten. Idempotent.  Usage: python lab/seesaw.py [--offline]
"""

import argparse
import io
import json
import os
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import m1  # noqa: E402

OUT = os.path.join(HERE, "seesaw")
START = pd.Timestamp("2026-10-06", tz="UTC")          # Monday 2026-10-05 close = 00:00 UTC Tuesday
BANKROLL = 5000.0
FEE_BPS, SLIP_BPS = 5, 5
WINDOW, SMOOTH, MIN_OBS, TOP_N, FRAC = 720, 72, 500, 50, 0.20
U = json.load(open(os.path.join(OUT, "universe.json"), encoding="utf-8"))
LARGE, FIRST = U["large"], {k: pd.Timestamp(v, tz="UTC") for k, v in U["first_trade"].items()}


def csv_load(name):
    p = os.path.join(OUT, f"{name}.csv")
    if not os.path.exists(p):
        return pd.DataFrame()
    df = pd.read_csv(p, index_col=0, encoding="utf-8")
    df.index = pd.to_datetime(df.index, utc=True)
    return df


def csv_save(df, name):
    df.sort_index().to_csv(os.path.join(OUT, f"{name}.csv"), float_format="%.10g", lineterminator="\n", encoding="utf-8")


def get_day(symbol, interval, day):
    """One daily archive file (1d or 1h candles) for `day`, or None if not published / not trading."""
    url = f"{m1.FILES}data/futures/um/daily/klines/{symbol}/{interval}/{symbol}-{interval}-{day:%Y-%m-%d}.zip"
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=60)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                raw = z.read(z.namelist()[0]).decode()
            d = pd.read_csv(io.StringIO(raw), header=0 if not raw[:1].isdigit() else None).iloc[:, :8]
            d.columns = ["open_ms", "open", "high", "low", "close", "volume", "close_ms", "quote_volume"]
            d = d.apply(pd.to_numeric)
            t = d["open_ms"].where(d["open_ms"] < 1e14, d["open_ms"] // 1000)
            return d.set_index(pd.to_datetime(t, unit="ms", utc=True))[["close", "quote_volume"]]
        except (requests.RequestException, zipfile.BadZipFile, ValueError):
            time.sleep(2 * (attempt + 1))
    return None


def fetch_many(jobs):
    """jobs: list of (symbol, interval, day) -> dict of results (None where missing)."""
    with ThreadPoolExecutor(16) as ex:
        return dict(zip(jobs, ex.map(lambda j: get_day(*j), jobs)))


def yesterday():
    return pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=1)


def refresh_daily(C, V):
    last_needed = yesterday()
    jobs = []
    for s in C.columns:
        lv = C[s].last_valid_index()
        if lv is None or lv < C.index.max() - pd.Timedelta(days=10):
            continue                                       # stopped trading: don't keep asking
        jobs += [(s, "1d", d) for d in pd.date_range(lv + pd.Timedelta(days=1), last_needed, freq="D")]
    for (s, _, d), x in fetch_many(jobs).items():
        if x is not None and len(x):
            C.loc[d, s] = float(x["close"].iloc[0])
            V.loc[d, s] = float(x["quote_volume"].iloc[0])
    return C.sort_index(), V.sort_index()


def weekly_lists(C, V):
    """{Monday close day: [50 coins]} for every Monday from the week before START on. Applies from the next day."""
    adv = V.rolling(14, min_periods=14).median()
    out = {}
    for m in C.index[(C.index.dayofweek == 0) & (C.index >= START - pd.Timedelta(days=8))]:
        ok = [s for s in C.columns if s not in LARGE and pd.notna(C.at[m, s]) and pd.notna(adv.at[m, s])
              and s in FIRST and FIRST[s] <= m - pd.Timedelta(days=59)]
        out[m] = list(adv.loc[m, ok].nlargest(TOP_N).index)
    return out


def refresh_hourly(H, need):
    """need: {symbol: [days]} of hourly candles required. Fetch the days not yet held."""
    jobs = []
    for s, days in need.items():
        have = set(H.index[H[s].notna()].normalize()) if s in H.columns else set()
        jobs += [(s, "1h", d) for d in days if d not in have]
    for (s, _, d), x in fetch_many(jobs).items():
        if x is not None and len(x):
            for t, v in x["close"].items():
                H.loc[t, s] = v
    return H.sort_index()


def refresh_funding(F, symbols):
    have = F.index.max().tz_convert(None).to_period("M") if len(F) else pd.Period("2026-08", "M")
    now_m = pd.Timestamp.now(tz="UTC").tz_convert(None).to_period("M")
    for m in pd.period_range(have + 1, now_m - 1, freq="M"):
        with ThreadPoolExecutor(16) as ex:
            got = dict(zip(symbols, ex.map(lambda s: m1.funding_month(s, str(m)), symbols)))
        got = {s: f for s, f in got.items() if f is not None}
        if not got:
            print(f"  funding for {m} not published yet")
            break
        F = pd.concat([F, pd.DataFrame(got)])
        print(f"  added funding for {m} ({len(got)} coins)")
    return F


def held_positions(H, lists, end_day):
    """Hourly target and held positions from START to the end of end_day (only data before each hour)."""
    hours = pd.date_range(START - pd.Timedelta(hours=SMOOTH), end_day + pd.Timedelta(hours=23), freq="h")
    R = H.reindex(pd.date_range(H.index.min(), hours[-1], freq="h")).pct_change(fill_method=None)
    coins = sorted({c for v in lists.values() for c in v})
    T = pd.DataFrame(0.0, index=hours, columns=coins)
    mondays = sorted(lists)
    for D in pd.date_range(START, end_day, freq="D"):
        mon = [m for m in mondays if m < D]                # list from the latest Monday close before day D
        if not mon:
            continue
        lst = [c for c in lists[mon[-1]] if c in R.columns]
        h0 = R.index.searchsorted(D)
        X = R[[c for c in LARGE if c in R.columns]].to_numpy(dtype=float)
        fit, today = slice(max(0, h0 - WINDOW), h0), slice(h0, h0 + 24)
        Xf, Xt = X[fit][:-1], X[today.start - 1:today.stop - 1]
        keep = ~np.isnan(Xf).any(axis=0) & ~np.isnan(Xt).any(axis=0)
        if keep.sum() == 0:
            continue
        Af = np.column_stack([np.ones(len(Xf)), Xf[:, keep]])
        At = np.column_stack([np.ones(len(Xt)), Xt[:, keep]])
        preds = {}
        for c in lst:
            y = R[c].to_numpy(dtype=float)[fit][1:]
            ok = ~np.isnan(y) & ~np.isnan(Af).any(axis=1)
            if ok.sum() >= MIN_OBS:
                preds[c] = At @ np.linalg.lstsq(Af[ok], y[ok], rcond=None)[0]
        if len(preds) < 10:
            continue
        P = pd.DataFrame(preds, index=R.index[today])
        n = P.notna().sum(axis=1).to_numpy()[:, None]
        k = np.maximum(1, np.floor(FRAC * n))
        hi = P.rank(axis=1, ascending=False).to_numpy() <= k
        lo = P.rank(axis=1, ascending=True).to_numpy() <= k
        T.loc[P.index, P.columns] = np.where(hi, 0.5 / k, np.where(lo, -0.5 / k, 0.0))
    held = T.rolling(SMOOTH, min_periods=1).mean()
    sel = held.index >= START
    return held[sel], R.reindex(held.index[sel])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    C, V, H, F = csv_load("daily_close"), csv_load("daily_volume"), csv_load("hourly"), csv_load("funding")
    if not a.offline:
        C, V = refresh_daily(C, V)
        csv_save(C, "daily_close"), csv_save(V, "daily_volume")
    lists = weekly_lists(C, V)
    pd.DataFrame([{"week": f"{m:%Y-%m-%d}", "rank": i + 1, "symbol": s} for m, v in lists.items() for i, s in enumerate(v)]
                 ).to_csv(os.path.join(OUT, "lists.csv"), index=False, lineterminator="\n", encoding="utf-8")
    end_day = min(C.index.max(), yesterday())                  # last complete day of daily data
    if not a.offline:
        need_coins = sorted(set(LARGE) | {c for v in lists.values() for c in v})
        days = list(pd.date_range(START - pd.Timedelta(days=WINDOW // 24 + 5), end_day, freq="D"))
        H = refresh_hourly(H, {s: days for s in need_coins})
        H = H[H.index >= START - pd.Timedelta(days=WINDOW // 24 + 6)]
        csv_save(H, "hourly")
        F = refresh_funding(F, sorted(set(C.columns)))
        csv_save(F, "funding")
    if end_day < START:
        open(os.path.join(OUT, "status.txt"), "w", newline="\n").write(
            f"data_through={end_day:%Y-%m-%d}\nfunding_through={F.index.max():%Y-%m-%d}\nupdated_utc=\n")
        print(f"seesaw: starts {START:%Y-%m-%d}; data through {end_day:%Y-%m-%d}; nothing to trade yet")
        return
    held, R = held_positions(H, lists, end_day)
    Y = R[held.columns].fillna(0.0)
    gross = (held * Y).sum(axis=1)
    turn = (held - held.shift(1).fillna(0.0)).abs().sum(axis=1)
    cost = turn * (FEE_BPS + SLIP_BPS) / 1e4
    Fh = (F.reindex(columns=held.columns).fillna(0.0) / 24).reindex(held.index.floor("D")).set_axis(held.index).fillna(0.0)
    fund = -(held * Fh).sum(axis=1)
    day = lambda s: s.groupby(s.index.floor("D")).sum()      # noqa: E731
    net_h = gross + fund - cost
    daily_net = (1 + net_h).groupby(net_h.index.floor("D")).prod() - 1
    btc = C["BTCUSDT"].pct_change(fill_method=None).reindex(daily_net.index).fillna(0.0)
    eq = pd.DataFrame({"date": [f"{d:%Y-%m-%d}" for d in daily_net.index], "gross": day(gross).round(6).values,
                       "funding": day(fund).round(6).values, "cost": day(cost).round(6).values,
                       "net": daily_net.round(6).values, "equity": (BANKROLL * (1 + daily_net).cumprod()).round(2).values,
                       "btc_equity": (BANKROLL * (1 + btc).cumprod()).round(2).values})
    eq.to_csv(os.path.join(OUT, "equity.csv"), index=False, lineterminator="\n")
    # each finished day's hourly held positions, written once and never rewritten
    pdir = os.path.join(OUT, "positions")
    os.makedirs(pdir, exist_ok=True)
    stamp = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M")
    for d, block in held.groupby(held.index.floor("D")):
        p = os.path.join(pdir, f"{d:%Y-%m-%d}.csv")
        if os.path.exists(p):
            continue
        long = block.stack()
        long = long[long.abs() > 1e-9].round(6).rename("held_weight").reset_index()
        long.columns = ["hour_utc", "symbol", "held_weight"]
        long["hour_utc"] = long["hour_utc"].dt.strftime("%Y-%m-%d %H:00")
        long["computed_utc"] = stamp
        long.to_csv(p, index=False, lineterminator="\n", encoding="utf-8")
    last = held.iloc[-1]
    last = last[last.abs() > 1e-9]
    pd.DataFrame({"symbol": last.index, "held_weight": last.round(6).values,
                  "close": [float(C[s].dropna().iloc[-1]) for s in last.index]}
                 ).to_csv(os.path.join(OUT, "latest.csv"), index=False, lineterminator="\n", encoding="utf-8")
    sp = os.path.join(OUT, "status.txt")
    old = dict(l.strip().split("=", 1) for l in open(sp) if "=" in l) if os.path.exists(sp) else {}
    st = {"data_through": f"{end_day:%Y-%m-%d}", "funding_through": f"{F.index.max():%Y-%m-%d}" if len(F) else "-"}
    st["updated_utc"] = old["updated_utc"] if all(old.get(k) == v for k, v in st.items()) and "updated_utc" in old else stamp
    open(sp, "w", newline="\n").write("".join(f"{k}={v}\n" for k, v in st.items()))
    print(f"seesaw paper equity ${eq['equity'].iloc[-1]:,.2f} after {len(eq)} day(s); {len(last)} positions held")


if __name__ == "__main__":
    main()
