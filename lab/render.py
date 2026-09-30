#!/usr/bin/env python3
"""
Render the Strategy lab pages from the committed lab files. Standard library only, no network:
it runs inside the 10-minute site build next to build.py.

  public/lab/index.html           overview + scoreboard           (lab/overview.html)
  public/lab/momentum/index.html  rule M1, the candidate          (lab/template.html)
  public/lab/controls/index.html  'just buy memecoins' and        (lab/controls.html)
                                  long-only momentum, the controls

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
CONTROLS = [
    {"id": "basket", "name": "Just buy memecoins",
     "what": "Holds every qualifying memecoin in equal amounts, re-set each Monday. No picking, no shorts.",
     "backtest": "Jan 2024 – Sep 2026 backtest: −54%, worst drop −91%."},
    {"id": "longonly", "name": "Long-only momentum",
     "what": "M1's picks with the shorts removed: only the recent winners, fully invested.",
     "backtest": "Jan 2024 – Sep 2026 backtest: +84%, but a worst drop of −89%."},
]


def rows(name):
    p = os.path.join(HERE, name)
    return list(csv.DictReader(open(p, encoding="utf-8"))) if os.path.exists(p) else []


def status():
    p = os.path.join(HERE, "status.txt")
    return dict(line.strip().split("=", 1) for line in open(p) if "=" in line) if os.path.exists(p) else {}


def book_of(pos, latest):
    """The latest week's positions with entry and latest close."""
    weeks = sorted({p["week"] for p in pos})
    cur = [p for p in pos if weeks and p["week"] == weeks[-1]]
    return weeks, [{"coin": p["symbol"].replace("USDT", ""), "side": p["side"], "weight": float(p["weight"]),
                    "entry": float(p["entry_close"]), "now": latest.get(p["symbol"]), "published": p["published_utc"]} for p in cur]


def equity_points(eq):
    return [{"d": r["date"], "e": float(r["equity"]), "b": float(r["btc_equity"])} for r in eq]


def weekly_history(eq):
    """Group daily rows by the Monday that started the holding week."""
    wk = defaultdict(lambda: {"net": 1.0, "cost": 0.0, "funding": 0.0, "days": 0})
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
    return [{"week": k, "ret": v["net"] - 1, "cost": v["cost"], "funding": v["funding"], "days": v["days"],
             "btc": v["btc_end"] / v["btc_start"] - 1} for k, v in sorted(wk.items())]


def summary(points):
    """Value, return since start and worst drop from a list of equity points."""
    peak, dd = BANKROLL, 0.0
    for p in points:
        peak = max(peak, p["e"])
        dd = min(dd, p["e"] / peak - 1)
    v = points[-1]["e"] if points else BANKROLL
    return {"value": v, "ret": v / BANKROLL - 1, "worst": dd}


def live_book(book):
    """What the overview needs to mark a strategy live: each coin's weight and last daily close."""
    return [{"coin": p["coin"], "weight": p["weight"] if p["side"] == "long" else -abs(p["weight"]), "now": p["now"]} for p in book]


def write(template, data, *out):
    css = open(os.path.join(HERE, "common.css"), encoding="utf-8").read()
    tpl = open(os.path.join(HERE, template), encoding="utf-8").read().replace("__CSS__", css)
    page = "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">" \
           "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">" \
           "<style>body{margin:0}</style></head><body>" \
           + tpl.replace("__DATA__", json.dumps(data).replace("</", "<\\/")) + "</body></html>"
    d = os.path.join(ROOT, "public", "lab", *out)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "index.html"), "w", encoding="utf-8").write(page)


def main():
    st = status()
    latest = {r["symbol"]: float(r["close"]) for r in rows("latest.csv")}

    # ---- M1, the candidate
    eq = rows("equity.csv")
    weeks, book = book_of(rows("positions.csv"), latest)
    proof_status = {}
    sp = os.path.join(HERE, "proofs", "status.json")
    if os.path.exists(sp):
        proof_status = json.load(open(sp))
    proofs = [{**r, "bitcoin_block": (proof_status.get(r["week"]) or {}).get("bitcoin_block")}
              for r in rows(os.path.join("proofs", "index.csv"))]
    band_path = os.path.join(HERE, "band.json")
    band = json.load(open(band_path)) if os.path.exists(band_path) else None
    m1_points = equity_points(eq)
    write("template.html", {
        "status": st, "bankroll": BANKROLL, "weeks": weeks, "book": book, "history": weekly_history(eq),
        "equity": m1_points, "review_weeks": REVIEW_WEEKS, "repo": REPO, "proofs": proofs, "band": band,
    }, "momentum")

    # ---- controls
    controls = []
    for c in CONTROLS:
        ceq = rows(os.path.join("controls", c["id"], "equity.csv"))
        cweeks, cbook = book_of(rows(os.path.join("controls", c["id"], "positions.csv")), latest)
        pts = equity_points(ceq)
        controls.append({**c, "equity": pts, "book": cbook, "weeks": cweeks, "history": weekly_history(ceq), **summary(pts)})
    write("controls.html", {"status": st, "bankroll": BANKROLL, "repo": REPO, "controls": controls,
                            "m1": m1_points}, "controls")

    # ---- overview / scoreboard
    btc = [{"d": p["d"], "e": p["b"]} for p in m1_points]
    board = [{"id": "momentum", "name": "Momentum rule (M1)", "kind": "candidate", "href": "/lab/momentum/",
              "what": "Bets on the memecoins that have been rising and against the ones that have been falling.",
              "weeks": len(weeks), "equity": m1_points, "book": live_book(book), **summary(m1_points)}]
    board += [{"id": c["id"], "name": c["name"], "kind": "control", "href": "/lab/controls/", "what": c["what"],
               "weeks": len(c["weeks"]), "equity": c["equity"], "book": live_book(c["book"]), **summary(c["equity"])} for c in controls]
    board.append({"id": "btc", "name": "Buy and hold BTC", "kind": "benchmark", "href": None,
                  "what": "For reference: what the same money would do sitting in Bitcoin.",
                  "weeks": len(weeks), "equity": btc, "book": [{"coin": "BTC", "weight": 1.0, "now": latest.get("BTCUSDT")}],
                  **summary(btc)})
    write("overview.html", {"status": st, "bankroll": BANKROLL, "repo": REPO, "board": board}, )

    print(f"built public/lab (overview, momentum, controls): {len(weeks)} week(s), {len(eq)} day(s) of results "
          f"(data through {html.escape(st.get('data_through', '?'))})")


if __name__ == "__main__":
    main()
