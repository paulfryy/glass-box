#!/usr/bin/env python3
"""
Momentum lab: forward paper-trading of rule M1, in public.

M1 (frozen 2026-09-27, before its holdout): each Monday at the daily close (00:00 UTC Tuesday),
among the memecoin perpetual futures in universe.yaml that have >= 14 days of history and a 14-day
median daily volume >= $2M, go long the top-k and short the bottom-k by L-day return. Four books,
L in {14, 30} days x k in {3, 5}, blended equally; per-coin cap 0.5/k per book. Positions are held
for the week. Costs: 0.10% fee + 0.15% slippage per unit traded, plus funding.

This script is idempotent:
  1. pulls new daily candles (and any newly published month of funding) from Binance's free
     public archive, data.binance.vision, into lab/data/*.csv
  2. appends each new Monday's positions to lab/positions.csv (existing rows are never rewritten;
     the git history of that file is the timestamped record)
  3. writes lab/equity.csv, the paper account since START

Usage: python lab/m1.py [--offline]
"""

import argparse
import io
import os
import re
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
FILES = "https://data.binance.vision/"
START = pd.Timestamp("2026-09-28", tz="UTC")          # first Monday close traded on paper
BANKROLL = 5000.0            # paper account size (results are the same in % terms)
FEE_BPS, SLIP_BPS = 10, 15
BOOKS = [(14, 3), (14, 5), (30, 3), (30, 5)]
MIN_AGE, MIN_ADV = 14, 2e6


# ---------------------------------------------------------------- data
def list_keys(prefix):
    keys, marker = [], None
    while True:
        p = {"prefix": prefix}
        if marker:
            p["marker"] = marker
        r = requests.get(S3, params=p, timeout=60).text
        ks = re.findall(r"<Key>([^<]+)</Key>", r)
        keys += ks
        if "<IsTruncated>true</IsTruncated>" not in r or not ks:
            return keys
        marker = ks[-1]


def read_zip(key):
    for attempt in range(4):
        try:
            r = requests.get(FILES + key, timeout=60)
            r.raise_for_status()
            break
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        raw = z.read(z.namelist()[0]).decode()
    header = 0 if not raw[:1].isdigit() else None
    return pd.read_csv(io.StringIO(raw), header=header)


def new_days(symbol, after):
    """Daily candles for `symbol` strictly after timestamp `after` (daily zips for recent months)."""
    months = pd.period_range(after.tz_convert(None).to_period("M"), pd.Timestamp.now(tz="UTC").tz_convert(None).to_period("M"), freq="M")
    rows = []
    for m in months:
        for k in list_keys(f"data/futures/um/daily/klines/{symbol}/1d/{symbol}-1d-{m}"):
            d = re.search(r"(\d{4}-\d{2}-\d{2})\.zip$", k)
            if d and pd.Timestamp(d.group(1), tz="UTC") > after:
                x = read_zip(k).iloc[:, :8]
                x.columns = ["open_ms", "open", "high", "low", "close", "volume", "close_ms", "quote_volume"]
                rows.append(x)
    if not rows:
        return None
    df = pd.concat(rows)
    df["time"] = pd.to_datetime(pd.to_numeric(df["open_ms"]), unit="ms", utc=True)
    return df.set_index("time")[["close", "quote_volume"]].apply(pd.to_numeric)


def funding_month(symbol, month):
    """Daily summed funding for one month, or None if Binance hasn't published it yet."""
    key = f"data/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{month}.zip"
    try:
        df = read_zip(key)
    except requests.HTTPError:
        return None
    df.columns = [c.strip() for c in df.columns]
    t = pd.to_datetime(pd.to_numeric(df[[c for c in df.columns if "time" in c.lower()][0]]), unit="ms", utc=True)
    r = pd.to_numeric(df[[c for c in df.columns if "rate" in c.lower()][0]])
    return r.groupby(t.dt.floor("D").values).sum()


def load(name):
    p = os.path.join(DATA, f"{name}.csv")
    df = pd.read_csv(p, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True)
    return df


def save(df, name):
    df.sort_index().to_csv(os.path.join(DATA, f"{name}.csv"), float_format="%.10g")


def refresh(symbols):
    C, V, F = load("closes"), load("volumes"), load("funding")
    last = C.index.max()

    def one(s):
        try:
            return s, new_days(s, last), None
        except Exception as e:
            return s, None, repr(e)
    with ThreadPoolExecutor(8) as ex:
        got = list(ex.map(one, symbols))
    for s, df, err in got:
        if err:
            print(f"  warning: {s}: {err}")
        if df is None:
            continue
        for t, row in df.iterrows():
            C.loc[t, s] = row["close"]
            V.loc[t, s] = row["quote_volume"]
    # funding: every completed month after the last month we hold, if it's published
    have = F.index.max().tz_convert(None).to_period("M") if len(F) else pd.Period("2024-12", "M")
    now_m = pd.Timestamp.now(tz="UTC").tz_convert(None).to_period("M")
    for m in pd.period_range(have + 1, now_m - 1, freq="M"):
        month = {}
        for s in symbols:
            f = funding_month(s, str(m))
            if f is not None:
                month[s] = f
        if not month:
            print(f"  funding for {m} not published yet")
            break
        F = pd.concat([F, pd.DataFrame(month)])
        print(f"  added funding for {m} ({len(month)} coins)")
    save(C, "closes"), save(V, "volumes"), save(F, "funding")
    print(f"closes through {C.index.max():%Y-%m-%d} (was {last:%Y-%m-%d})")
    return C, V, F


# ---------------------------------------------------------------- rule (same code as the backtest)
def eligible(C, V):
    age = C.notna().cumsum()
    adv = V.rolling(14, min_periods=14).median()
    alive_next = C.shift(-1).notna()
    return (age >= MIN_AGE) & (adv >= MIN_ADV) & alive_next & C.notna()


def live_eligible(C, V):
    """On the newest day, tomorrow's candle doesn't exist yet: assume today's coins still trade."""
    nxt = C.index[-1] + pd.Timedelta(days=1)
    pad = lambda X: pd.concat([X, X.iloc[[-1]].set_axis([nxt])])  # noqa: E731
    return eligible(pad(C), pad(V)).iloc[:-1]


def norm(S, cap):
    n = S.abs().sum(axis=1).replace(0, np.nan)
    return S.div(n, axis=0).clip(-cap, cap).fillna(0.0)


def book(C, E, L, k):
    ret = C.pct_change(L, fill_method=None).where(E)
    S = pd.DataFrame(0.0, index=C.index, columns=C.columns)
    S[ret.rank(axis=1, ascending=False) <= k] = 1.0
    S[(ret.rank(axis=1, ascending=True) <= k) & (S == 0)] = -1.0
    S = S.where(E, 0.0)
    monday = pd.Series(S.index.dayofweek == 0, index=S.index)
    S = S.where(monday, np.nan).ffill().fillna(0.0).where(E, 0.0)
    return norm(S, cap=0.5 / k)


def weights(C, E):
    return sum(book(C, E, L, k) for L, k in BOOKS) / len(BOOKS)


def run(W, C, F):
    W = W.reindex_like(C).fillna(0.0)
    R = C.pct_change(fill_method=None).shift(-1).fillna(0.0)
    Fn = F.reindex_like(C).fillna(0.0).shift(-1).fillna(0.0)
    gross = (W * R).sum(axis=1)
    fund = -(W * Fn).sum(axis=1)
    drift = (W * (1 + R)).div((1 + (W * R).sum(axis=1)).replace(0, np.nan), axis=0).shift(1).fillna(0.0)
    cost = (W - drift).abs().sum(axis=1) * (FEE_BPS + SLIP_BPS) / 1e4
    return pd.DataFrame({"gross": gross, "funding": fund, "cost": cost, "net": gross + fund - cost})


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="skip the download, recompute from lab/data")
    a = ap.parse_args()
    symbols = yaml.safe_load(open(os.path.join(HERE, "universe.yaml"), encoding="utf-8"))["perps"]
    if a.offline:
        C, V, F = load("closes"), load("volumes"), load("funding")
    else:
        C, V, F = refresh(symbols + ["BTCUSDT"])
    btc = C.pop("BTCUSDT")
    V = V.drop(columns=["BTCUSDT"], errors="ignore")
    F = F.drop(columns=["BTCUSDT"], errors="ignore")
    C, V = C[symbols], V[symbols]
    E = live_eligible(C, V)
    W = weights(C, E)

    # 1. positions: append each new Monday close (never rewrite published rows)
    pos_path = os.path.join(HERE, "positions.csv")
    pos = pd.read_csv(pos_path) if os.path.exists(pos_path) else pd.DataFrame(
        columns=["week", "symbol", "side", "weight", "entry_close", "published_utc"])
    done = set(pos["week"].astype(str))
    stamp = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M")
    add = []
    for m in W.index[(W.index.dayofweek == 0) & (W.index >= START)]:
        if f"{m:%Y-%m-%d}" in done:
            continue
        w = W.loc[m][W.loc[m].abs() > 1e-9].sort_values(ascending=False)
        add += [{"week": f"{m:%Y-%m-%d}", "symbol": s, "side": "long" if x > 0 else "short",
                 "weight": round(float(x), 6), "entry_close": float(C.loc[m, s]), "published_utc": stamp} for s, x in w.items()]
    if add:
        pos = pd.concat([pos, pd.DataFrame(add)], ignore_index=True)
        pos.to_csv(pos_path, index=False)
        print(f"published positions for {sorted({r['week'] for r in add})}")

    # 2. paper account since START (row t = return from close t to close t+1; the newest row has none yet)
    r = run(W[W.index >= START], C[C.index >= START], F[F.index >= START]).iloc[:-1]
    b = btc.pct_change(fill_method=None).shift(-1).reindex(r.index).fillna(0.0)
    eq = pd.DataFrame({"date": [f"{d + pd.Timedelta(days=1):%Y-%m-%d}" for d in r.index],   # value AT that close
                       "gross": r["gross"].round(6), "funding": r["funding"].round(6), "cost": r["cost"].round(6),
                       "net": r["net"].round(6), "equity": (BANKROLL * (1 + r["net"]).cumprod()).round(2),
                       "btc_equity": (BANKROLL * (1 + b).cumprod()).round(2)})
    eq.to_csv(os.path.join(HERE, "equity.csv"), index=False)
    latest = {s: float(C[s].dropna().iloc[-1]) for s in C.columns if C[s].notna().any()}
    pd.Series(latest, name="close").to_csv(os.path.join(HERE, "latest.csv"), index_label="symbol")
    # status: only touch the timestamp when the data actually moved, so idle runs commit nothing
    sp = os.path.join(HERE, "status.txt")
    old = dict(line.strip().split("=", 1) for line in open(sp) if "=" in line) if os.path.exists(sp) else {}
    st = {"data_through": f"{C.index.max():%Y-%m-%d}", "funding_through": f"{F.index.max():%Y-%m-%d}"}
    same = all(old.get(k) == v for k, v in st.items()) and "updated_utc" in old
    st["updated_utc"] = old["updated_utc"] if same else stamp
    open(sp, "w").write("".join(f"{k}={v}\n" for k, v in st.items()))
    print(f"paper equity ${eq['equity'].iloc[-1] if len(eq) else BANKROLL:,.2f} after {len(eq)} day(s)")


if __name__ == "__main__":
    main()
