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


NAV = [{"id": "overview", "label": "Overview", "href": "/lab/"},
       {"id": "momentum", "label": "Momentum (M1)", "href": "/lab/momentum/"},
       {"id": "carry", "label": "Carry", "href": "/lab/carry/"},
       {"id": "seesaw", "label": "Seesaw", "href": "/lab/seesaw/"},
       {"id": "controls", "label": "Controls", "href": "/lab/controls/"}]
FORWARD = [
    {"id": "carry", "name": "BTC + ETH cash-and-carry", "start": "2026-10-05", "cp13": "2027-01-04", "cp26": "2027-04-05",
     "cash_line": True,
     "short": "Holds BTC and ETH while shorting the same amount of their futures, collecting the funding leveraged buyers pay.",
     "what": "Half the account buys BTC and ETH; the other half backs an equal short in their perpetual futures. Price moves "
             "cancel out, so the account earns (or pays) funding: the fee that leveraged long traders usually pay shorts.",
     "why": "It's here because it was the best version in our carry backtest (+4.5% a year, worst drop −1.2%), but it wasn't "
            "the version we pre-registered as primary, so its past doesn't count as evidence.",
     "update_note": "Updated daily around 09:30 UTC.",
     "positions_title": "Positions (re-set each Monday close)",
     "positions_note": "Long spot and short futures in equal amounts, so price moves cancel.",
     "bar": "Verdict: 'still alive' only if the account beats cash (4% a year, about +1.96% over 26 weeks) after costs.",
     "caveats": ["Funding has been low lately: our backtest made about 0% in 2025–2026, and the Aug–Sep 2026 replay earned "
                 "funding at only about 2.8% a year.",
                 "Binance publishes funding monthly, so the most recent weeks don't include it yet and will be revised.",
                 "The futures leg is on Binance, which isn't available to US residents (Coinbase offers US-regulated "
                 "BTC and ETH perpetual-style futures with different funding)."],
     "rules": ["Half the account buys BTC and ETH spot in equal amounts; the other half is margin for an equal short in each "
               "coin's Binance USD-M perpetual future.",
               "Re-set to equal weights each Monday close; brought back to target at each daily close.",
               "Costs: spot 0.40% fee + 0.05% slippage, futures 0.05% fee + 0.05% slippage, on every dollar traded; funding as published.",
               "Data: Binance's free public archive (data.binance.vision)."],
     "proof_note": "Each week's positions file is committed by an automated GitHub job when it's published."},
    {"id": "seesaw", "name": "Seesaw (S1-smooth72)", "start": "2026-10-05", "cp13": "2027-01-04", "cp26": "2027-04-05",
     "cash_line": False,
     "short": "Bets on altcoins moving opposite to the big coins' last move, averaged over 3 days to keep trading costs down.",
     "what": "When the biggest coins (BTC, ETH, XRP, LTC, EOS) move, other coins tend to move the opposite way in the next hour, "
             "a 'seesaw' effect from published research. This account goes long the 50 most-traded altcoins predicted to rise "
             "and short those predicted to fall, averaging its positions over the last 72 hours so it trades slowly enough to "
             "cover costs.",
     "why": "It's here because it was our closest near-miss: positive after costs in a 2024–2026 test (+15% a year) but too "
            "dependent on 2026 and a few big days to pass.",
     "update_note": "Positions are recalculated every hour from data available before that hour; the account is updated "
                    "daily around 09:30 UTC.",
     "positions_title": "Positions at the latest hour",
     "positions_note": "Positions change a little every hour as the 72-hour average moves.",
     "bar": "Verdict: 'still alive' only if the account is up after taker costs and its daily Sharpe ratio is at least 0.5.",
     "caveats": ["Hourly positions are computed once a day, after the fact, by fixed public code using only data from before "
                 "each hour. They can't be published hour by hour, so the safeguard is that the rule leaves no choices: anyone "
                 "can re-run the code on the published files.",
                 "The underlying effect is real but small; in testing, the account's results depended on a few big days.",
                 "These are offshore Binance futures, not available to US residents, and real trading would need limit "
                 "orders to keep costs down. Treat this as research only."],
     "rules": ["Coins: each Monday close, the 50 most-traded eligible Binance USD-M perpetuals from a fixed list of 712 "
               "(frozen Oct 3, 2026), excluding BTC, ETH, XRP, LTC and EOS. Each week's list is published in lists.csv.",
               "Prediction: for each coin, a least-squares fit of its next-hour return on the five big coins' latest hourly "
               "returns over the previous 720 hours, refitted daily at 00:00 UTC.",
               "Target every hour: long the 20% with the highest prediction, short the 20% with the lowest, each side half the account.",
               "Held position: the average of the last 72 hourly targets.",
               "Costs: 0.05% fee + 0.05% slippage on every dollar traded; funding charged hourly as published.",
               "Data: Binance's free public archive (data.binance.vision)."],
     "proof_note": "Each day's hourly positions file is committed by an automated GitHub job the next morning."},
]


def rows(name):
    p = os.path.join(HERE, name)
    return list(csv.DictReader(open(p, encoding="utf-8"))) if os.path.exists(p) else []


def status(name="status.txt"):
    p = os.path.join(HERE, name)
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


def write(template, data, *out, title=None):
    css = open(os.path.join(HERE, "common.css"), encoding="utf-8").read()
    tpl = open(os.path.join(HERE, template), encoding="utf-8").read().replace("__CSS__", css)
    if title:
        tpl = tpl.replace("__TITLE__", html.escape(title))
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
    # ---- forward tests (started 2026-10-05; lab/forward_protocol.md)
    for f in FORWARD:
        fid = f["id"]
        fst = status(os.path.join(fid, "status.txt"))
        feq = rows(os.path.join(fid, "equity.csv"))
        pts = equity_points(feq)
        fl = {r["symbol"]: float(r["close"]) for r in rows(os.path.join(fid, "latest.csv")) if "close" in r}
        if fid == "carry":
            pos = rows(os.path.join(fid, "positions.csv"))
            wk = sorted({p["week"] for p in pos})
            fbook = [{"coin": p["symbol"].replace("USDT", ""), "leg": p["leg"], "weight": float(p["weight"]),
                      "ref": float(p["entry_close"])} for p in pos if wk and p["week"] == wk[-1]]
        else:
            fbook = [{"coin": r["symbol"].replace("USDT", ""), "weight": float(r["held_weight"]), "ref": float(r["close"])}
                     for r in rows(os.path.join(fid, "latest.csv"))]
        pst = json.load(open(os.path.join(HERE, "proofs", fid, "status.json"))) \
            if os.path.exists(os.path.join(HERE, "proofs", fid, "status.json")) else {}
        fproofs = [{**r, "bitcoin_block": (pst.get(r["week"]) or {}).get("bitcoin_block")}
                   for r in rows(os.path.join("proofs", fid, "index.csv"))]
        write("forward.html", {"status": fst, "bankroll": BANKROLL, "repo": REPO, "strategy": f, "nav": NAV,
                               "equity": pts, "history": weekly_history(feq), "book": fbook, "proofs": fproofs},
              fid, title=f["name"])
        board.insert(1 + FORWARD.index(f), {"id": fid, "name": f["name"], "kind": "candidate", "href": f"/lab/{fid}/",
                                             "what": f["short"], "start": f["start"], "equity": pts, "book": [],
                                             **summary(pts)})
    write("overview.html", {"status": st, "bankroll": BANKROLL, "repo": REPO, "board": board}, )

    print(f"built public/lab (overview, momentum, controls): {len(weeks)} week(s), {len(eq)} day(s) of results "
          f"(data through {html.escape(st.get('data_through', '?'))})")


if __name__ == "__main__":
    main()
