#!/usr/bin/env python3
"""
Render public/lab/index.html from the committed lab files (positions.csv, equity.csv,
latest.csv, status.txt). Standard library only, no network: it runs inside the 10-minute
site build next to build.py.

Usage: python lab/render.py
"""

import csv
import html
import json
import os
from collections import defaultdict
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REPO = "https://github.com/paulfryy/glass-box"
BANKROLL = 5000.0            # paper account size (results are the same in % terms)
REVIEW_WEEKS = (13, 26)


def rows(name):
    p = os.path.join(HERE, name)
    return list(csv.DictReader(open(p, encoding="utf-8"))) if os.path.exists(p) else []


def status():
    p = os.path.join(HERE, "status.txt")
    return dict(line.strip().split("=", 1) for line in open(p) if "=" in line) if os.path.exists(p) else {}


def main():
    st = status()
    pos = rows("positions.csv")
    eq = rows("equity.csv")
    latest = {r["symbol"]: float(r["close"]) for r in rows("latest.csv")}
    weeks = sorted({p["week"] for p in pos})
    cur = [p for p in pos if weeks and p["week"] == weeks[-1]]
    book = [{"coin": p["symbol"].replace("USDT", ""), "side": p["side"], "weight": float(p["weight"]),
             "entry": float(p["entry_close"]), "now": latest.get(p["symbol"]), "published": p["published_utc"]} for p in cur]

    # weekly results: group daily rows by the Monday that started the holding week
    wk = defaultdict(lambda: {"net": 1.0, "btc": None, "cost": 0.0, "funding": 0.0, "days": 0})
    prev_btc = BANKROLL
    for r in eq:
        d = date.fromisoformat(r["date"]) - timedelta(days=1)          # the close the day's return started from
        monday = d - timedelta(days=d.weekday())
        w = wk[monday.isoformat()]
        w["net"] *= 1 + float(r["net"])
        w["cost"] += float(r["cost"])
        w["funding"] += float(r["funding"])
        w["days"] += 1
        w["btc_start"] = w.get("btc_start", prev_btc)
        w["btc_end"] = float(r["btc_equity"])
        prev_btc = float(r["btc_equity"])
    history = [{"week": k, "ret": v["net"] - 1, "cost": v["cost"], "funding": v["funding"], "days": v["days"],
                "btc": v["btc_end"] / v["btc_start"] - 1} for k, v in sorted(wk.items())]

    # tamper-evidence: per-week SHA-256, stamp time, Actions run, Bitcoin block (lab/proof.py)
    proof_status = {}
    sp = os.path.join(HERE, "proofs", "status.json")
    if os.path.exists(sp):
        proof_status = json.load(open(sp))
    proofs = [{**r, "bitcoin_block": (proof_status.get(r["week"]) or {}).get("bitcoin_block")}
              for r in rows(os.path.join("proofs", "index.csv"))]
    band_path = os.path.join(HERE, "band.json")
    band = json.load(open(band_path)) if os.path.exists(band_path) else None

    data = {
        "status": st, "bankroll": BANKROLL, "weeks": weeks, "book": book, "history": history,
        "equity": [{"d": r["date"], "e": float(r["equity"]), "b": float(r["btc_equity"])} for r in eq],
        "review_weeks": REVIEW_WEEKS, "repo": REPO, "proofs": proofs, "band": band,
    }
    tpl = open(os.path.join(HERE, "template.html"), encoding="utf-8").read()
    page = "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">" \
           "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">" \
           "<style>body{margin:0}</style></head><body>" \
           + tpl.replace("__DATA__", json.dumps(data).replace("</", "<\\/")) + "</body></html>"
    out = os.path.join(ROOT, "public", "lab")
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, "index.html"), "w", encoding="utf-8").write(page)
    print(f"built public/lab/index.html: {len(weeks)} week(s) published, {len(eq)} day(s) of results "
          f"(data through {html.escape(st.get('data_through', '?'))})")


if __name__ == "__main__":
    main()
