#!/usr/bin/env python3
"""
Forward test F1: BTC + ETH cash-and-carry (lab/forward_protocol.md, fixed 2026-10-03).

Half the account buys BTC and ETH spot (equal amounts); the other half is margin for an equal short in each
coin's Binance USD-M perpetual. Price moves cancel; the position collects funding. Re-set each Monday close,
brought back to target at each daily close. Costs: spot 0.40% + 0.05%, perp 0.05% + 0.05% per unit traded.

Idempotent, like m1.py:
  1. pulls new daily spot and perp closes (and newly published months of funding) into lab/carry/data.csv
  2. appends each new Monday's positions to lab/carry/positions.csv (never rewritten)
  3. writes lab/carry/equity.csv, the paper account from START

Usage: python lab/carry.py [--offline]
"""

import argparse
import os
import re
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import m1  # noqa: E402  (shared archive helpers and the backtest engine)

OUT = os.path.join(HERE, "carry")
DATA = os.path.join(OUT, "data.csv")
START = pd.Timestamp("2026-10-05", tz="UTC")           # first Monday close traded on paper
BANKROLL = 5000.0
COINS = ["BTC", "ETH"]
SPOT_COST, PERP_COST = (40, 5), (5, 5)


def spot_days(symbol, after):
    """Daily spot candles strictly after `after`. Spot archive timestamps are microseconds since 2025."""
    rows = []
    months = pd.period_range(after.tz_convert(None).to_period("M"), pd.Timestamp.now(tz="UTC").tz_convert(None).to_period("M"), freq="M")
    for m in months:
        for k in m1.list_keys(f"data/spot/daily/klines/{symbol}/1d/{symbol}-1d-{m}"):
            d = re.search(r"(\d{4}-\d{2}-\d{2})\.zip$", k)
            if d and pd.Timestamp(d.group(1), tz="UTC") > after:
                x = m1.read_zip(k).iloc[:, :5]
                x.columns = ["open_ms", "open", "high", "low", "close"]
                rows.append(x)
    if not rows:
        return None
    df = pd.concat(rows).apply(pd.to_numeric)
    t = df["open_ms"].where(df["open_ms"] < 1e14, df["open_ms"] // 1000)
    return df.set_index(pd.to_datetime(t, unit="ms", utc=True))["close"]


def refresh(D):
    last = D.index.max()
    for c in COINS:                      # each series catches up from its own last day, so gaps can't persist
        s = spot_days(f"{c}USDT", D[f"{c}_spot"].last_valid_index())
        if s is not None:
            for t, v in s.items():
                D.loc[t, f"{c}_spot"] = v
        p = m1.new_days(f"{c}USDT", D[f"{c}_perp"].last_valid_index())
        if p is not None:
            for t, v in p["close"].items():
                D.loc[t, f"{c}_perp"] = v
    # funding: every completed month after the last month with any funding recorded
    fcols = [f"{c}_funding" for c in COINS]
    have = D[fcols].dropna(how="all").index.max().tz_convert(None).to_period("M")
    now_m = pd.Timestamp.now(tz="UTC").tz_convert(None).to_period("M")
    for m in pd.period_range(have + 1, now_m - 1, freq="M"):
        got = {c: m1.funding_month(f"{c}USDT", str(m)) for c in COINS}
        if any(v is None for v in got.values()):
            print(f"  funding for {m} not published yet")
            break
        for c, f in got.items():
            for t, v in f.items():
                D.loc[t, f"{c}_funding"] = v
        print(f"  added funding for {m}")
    D = D.sort_index()
    D.to_csv(DATA, float_format="%.10g", lineterminator="\n")
    print(f"carry data through {D.index.max():%Y-%m-%d} (was {last:%Y-%m-%d})")
    return D


def weights(idx):
    """Equal weight across BTC and ETH; half the account on each leg."""
    w = pd.DataFrame(0.0, index=idx, columns=COINS)
    w.loc[idx >= START, :] = 0.5 / len(COINS)
    return w


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    D = pd.read_csv(DATA, index_col=0)
    D.index = pd.to_datetime(D.index, utc=True)
    if not a.offline:
        D = refresh(D)
    S = D[[f"{c}_spot" for c in COINS]].set_axis(COINS, axis=1)
    P = D[[f"{c}_perp" for c in COINS]].set_axis(COINS, axis=1)
    funding_through = D[[f"{c}_funding" for c in COINS]].dropna(how="all").index.max()
    Fz = D[[f"{c}_funding" for c in COINS]].set_axis(COINS, axis=1).fillna(0.0)
    zero = pd.DataFrame(0.0, index=D.index, columns=COINS)
    complete = S.notna().all(axis=1) & P.notna().all(axis=1)
    S, P, Fz, zero = S[complete], P[complete], Fz[complete], zero[complete]
    W = weights(S.index)
    stamp = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M")

    # 1. positions: one row per coin and leg for each new Monday close (never rewritten)
    pos_path = os.path.join(OUT, "positions.csv")
    pos = pd.read_csv(pos_path) if os.path.exists(pos_path) else pd.DataFrame(
        columns=["week", "symbol", "leg", "side", "weight", "entry_close", "published_utc"])
    done = set(pos["week"].astype(str))
    add = []
    for m in S.index[(S.index.dayofweek == 0) & (S.index >= START)]:
        if f"{m:%Y-%m-%d}" in done:
            continue
        for c in COINS:
            add.append({"week": f"{m:%Y-%m-%d}", "symbol": f"{c}USDT", "leg": "spot", "side": "long",
                        "weight": W.at[m, c], "entry_close": float(S.at[m, c]), "published_utc": stamp})
            add.append({"week": f"{m:%Y-%m-%d}", "symbol": f"{c}USDT", "leg": "perp", "side": "short",
                        "weight": -W.at[m, c], "entry_close": float(P.at[m, c]), "published_utc": stamp})
    if add:
        pd.concat([pos, pd.DataFrame(add)], ignore_index=True).to_csv(pos_path, index=False, lineterminator="\n")
        print(f"published carry positions for {sorted({r['week'] for r in add})}")

    # 2. paper account: spot leg + perp leg (the short receives funding when it's positive)
    sel = S.index >= START
    s = m1.run(W[sel], S[sel], zero[sel], *SPOT_COST, funding=False)
    p = m1.run(-W[sel], P[sel], Fz[sel], *PERP_COST, funding=True)
    r = pd.DataFrame({"gross": s["gross"] + p["gross"], "funding": p["funding"], "cost": s["cost"] + p["cost"]})
    r["net"] = r["gross"] + r["funding"] - r["cost"]
    r = r.iloc[:-1] if len(r) > 1 else r.iloc[0:0]
    btc = S["BTC"][sel].pct_change(fill_method=None).shift(-1).reindex(r.index).fillna(0.0)
    eq = pd.DataFrame({"date": [f"{d + pd.Timedelta(days=1):%Y-%m-%d}" for d in r.index],
                       "gross": r["gross"].round(6), "funding": r["funding"].round(6), "cost": r["cost"].round(6),
                       "net": r["net"].round(6), "equity": (BANKROLL * (1 + r["net"]).cumprod()).round(2),
                       "btc_equity": (BANKROLL * (1 + btc).cumprod()).round(2)})
    eq.to_csv(os.path.join(OUT, "equity.csv"), index=False, lineterminator="\n")
    latest = {f"{c}USDT_spot": float(S[c].iloc[-1]) for c in COINS} | {f"{c}USDT_perp": float(P[c].iloc[-1]) for c in COINS}
    pd.Series(latest, name="close").to_csv(os.path.join(OUT, "latest.csv"), index_label="symbol", lineterminator="\n")
    sp = os.path.join(OUT, "status.txt")
    old = dict(l.strip().split("=", 1) for l in open(sp) if "=" in l) if os.path.exists(sp) else {}
    st = {"data_through": f"{S.index.max():%Y-%m-%d}", "funding_through": f"{funding_through:%Y-%m-%d}"}
    st["updated_utc"] = old["updated_utc"] if all(old.get(k) == v for k, v in st.items()) and "updated_utc" in old else stamp
    open(sp, "w", newline="\n").write("".join(f"{k}={v}\n" for k, v in st.items()))
    print(f"carry paper equity ${eq['equity'].iloc[-1] if len(eq) else BANKROLL:,.2f} after {len(eq)} day(s)")


if __name__ == "__main__":
    main()
