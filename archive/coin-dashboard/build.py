#!/usr/bin/env python3
"""
Glass Box Coin: build a live transparency page for one pump.fun coin.

Reads config.yaml (which coin), pulls its public on-chain history from Helius,
computes holders / snipers / creator position / red-flag checks, and writes
public/index.html. Run by a scheduled GitHub Action; the Helius key comes from
the HELIUS_API_KEY environment variable (a repository secret), never from files.

Local test:  HELIUS_API_KEY=... python build.py
"""

import base64
import hashlib
import json
import os
import struct
import sys
import time
from collections import defaultdict

import base58
import requests
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
EVENT_TAG = bytes.fromhex("e445a52e51cb9a1d")
SUPPLY = 1_000_000_000
GRAD_REAL_SOL = 85.0
SNIPER_WINDOW_S = 2


def disc(name):
    return hashlib.sha256(f"event:{name}".encode()).digest()[:8]


CREATE, TRADE, MIGRATE = disc("CreateEvent"), disc("TradeEvent"), disc("CompletePumpAmmMigrationEvent")


def pk(b, o):
    return base58.b58encode(b[o:o + 32]).decode()


def u64(b, o):
    return struct.unpack_from("<Q", b, o)[0]


def helius(key, method, params, timeout=90, attempts=5):
    """One Helius JSON-RPC call, retried on error responses and on dropped connections."""
    last = None
    for attempt in range(attempts):
        try:
            r = requests.post(f"https://mainnet.helius-rpc.com/?api-key={key}", timeout=timeout,
                              json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            if r.status_code == 200 and "result" in r.json():
                return r.json()["result"]
            last = f"{r.status_code} {r.text[:200]}"
        except (requests.RequestException, ValueError) as e:     # connection reset, timeout, bad JSON
            last = repr(e)
        time.sleep(2 ** attempt)
    raise RuntimeError(f"Helius {method} failed after {attempts} attempts: {last}")


def rpc_txs(key, address, t_start, t_end, max_pages):
    """All successful txs touching `address` in [t_start, t_end], oldest first (10 credits/page)."""
    opts = {"transactionDetails": "full", "sortOrder": "asc", "limit": 100, "encoding": "json",
            "maxSupportedTransactionVersion": 1,
            "filters": {"blockTime": {"gte": int(t_start), "lte": int(t_end)}, "status": "succeeded"}}
    out, token = [], None
    for _ in range(max_pages):
        o = dict(opts, **({"paginationToken": token} if token else {}))
        res = helius(key, "getTransactionsForAddress", [address, o])
        out += res.get("data") or []
        token = res.get("paginationToken")
        if not token or not res.get("data"):
            return out, False
    return out, True


def rpc(key, method, params):
    return helius(key, method, params, timeout=60)


def events(tx):
    lw = tx["meta"].get("loadedAddresses") or {}
    keys = tx["transaction"]["message"]["accountKeys"] + lw.get("writable", []) + lw.get("readonly", [])
    for grp in tx["meta"].get("innerInstructions") or []:
        for ix in grp["instructions"]:
            if keys[ix["programIdIndex"]] == PUMP:
                b = base58.b58decode(ix["data"])
                if b[:8] == EVENT_TAG:
                    yield b[8:16], b[16:]


def read_str(p, o):
    n = struct.unpack_from("<I", p, o)[0]
    return p[o + 4:o + 4 + n].decode("utf-8", "replace"), o + 4 + n


def decode_create(p):
    name, o = read_str(p, 0)
    symbol, o = read_str(p, o)
    uri, o = read_str(p, o)
    d = {"name": name.strip(), "symbol": symbol.strip(), "uri": uri, "mint": pk(p, o),
         "creator_signer": pk(p, o + 64), "creator": pk(p, o + 96),
         "v_tok0": u64(p, o + 136), "v_sol0": u64(p, o + 144)}
    o += 200
    d["mayhem"] = bool(p[o]) if len(p) > o else False
    d["cashback"] = bool(p[o + 1]) if len(p) > o + 1 else False
    d["quote"] = pk(p, o + 2) if len(p) >= o + 34 else "1" * 32
    return d


def decode_trade(p):
    return {"mint": pk(p, 0), "sol": u64(p, 32) / 1e9, "tok": u64(p, 40) / 1e6, "is_buy": bool(p[48]),
            "trader": pk(p, 49), "v_sol": u64(p, 89) / 1e9, "v_tok": u64(p, 97) / 1e6, "r_sol": u64(p, 105) / 1e9,
            "creator_fee_bps": u64(p, 201)}


def sol_usd():
    """SOL price in USD from keyless public tickers (CoinGecko now refuses keyless requests)."""
    sources = [
        ("https://api.gateio.ws/api/v4/spot/tickers?currency_pair=SOL_USDT", lambda j: j[0]["last"]),
        ("https://www.okx.com/api/v5/market/ticker?instId=SOL-USDT", lambda j: j["data"][0]["last"]),
        ("https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd", lambda j: j["solana"]["usd"]),
    ]
    for url, pick in sources:
        try:
            p = float(pick(requests.get(url, timeout=20).json()))
            if p > 0:
                return p
        except Exception:
            continue
    return None


def snapshot(key, mint, lookback_days, max_pages):
    now = int(time.time())
    txs, truncated = rpc_txs(key, mint, now - lookback_days * 86400, now, max_pages)
    create, trades, migrated = None, [], None
    for tx in txs:
        for d, p in events(tx):
            if d == CREATE:
                c = decode_create(p)
                if c["mint"] == mint:
                    create = {**c, "time": tx["blockTime"]}
            elif d == TRADE:
                t = decode_trade(p)
                if t["mint"] == mint:
                    trades.append({**t, "time": tx["blockTime"], "slot": tx["slot"],
                                   "sig": tx["transaction"]["signatures"][0]})
            elif d == MIGRATE and pk(p, 32) == mint:
                migrated = tx["blockTime"]
    if create is None:
        raise SystemExit(f"Could not find the creation of {mint} in the last {lookback_days} days.")
    creators = {create["creator"], create["creator_signer"]}
    t0 = create["time"]
    trades.sort(key=lambda t: t["slot"])
    held, first_buy = defaultdict(float), {}
    creator_bought, creator_sold, fees = 0.0, False, 0.0
    for t in trades:
        held[t["trader"]] += t["tok"] if t["is_buy"] else -t["tok"]
        if t["is_buy"]:
            first_buy.setdefault(t["trader"], t["time"])
        if t["trader"] in creators:
            creator_bought += t["tok"] if t["is_buy"] else 0
            creator_sold |= not t["is_buy"]
        fees += t["sol"] * t["creator_fee_bps"] / 1e4
    holders = {w: v for w, v in held.items() if v > 1}
    snipers = {w for w, ts in first_buy.items() if w not in creators and ts - t0 <= SNIPER_WINDOW_S}
    launch_bought = sum(t["tok"] for t in trades if t["is_buy"] and t["trader"] not in creators
                        and t["time"] - t0 <= SNIPER_WINDOW_S)
    mi = ((rpc(key, "getAccountInfo", [mint, {"encoding": "jsonParsed"}]) or {}).get("value") or {})
    mi = (mi.get("data") or {}).get("parsed", {}).get("info", {}) if isinstance(mi.get("data"), dict) else {}
    last = trades[-1] if trades else None
    v_sol = last["v_sol"] if last else create["v_sol0"] / 1e9
    v_tok = last["v_tok"] if last else create["v_tok0"] / 1e6
    r_sol = last["r_sol"] if last else 0.0
    price = v_sol / v_tok
    top = sorted(holders.items(), key=lambda kv: -kv[1])
    top10 = sum(v for _, v in top[:10])
    sniper_now = sum(holders.get(w, 0) for w in snipers)
    creator_now = sum(holders.get(w, 0) for w in creators)
    ext = [t for t in trades if t["trader"] not in creators]
    usd = sol_usd()
    tag = lambda w: "creator" if w in creators else ("launch sniper" if w in snipers else "")  # noqa: E731
    flags = [
        ("Mint authority revoked (no one can print more tokens)", not mi.get("mintAuthority"),
         "still active" if mi.get("mintAuthority") else "revoked"),
        ("Freeze authority revoked (your tokens can't be frozen)", not mi.get("freezeAuthority"),
         "still active" if mi.get("freezeAuthority") else "revoked"),
        ("Launch buyers took < 20% of supply", launch_bought < 0.20 * SUPPLY,
         f"{len(snipers)} wallet(s) bought {launch_bought / SUPPLY:.2%} within 2 s of launch"),
        ("Creator's launch buy is under 3% of supply", creator_bought < 0.03 * SUPPLY, f"{creator_bought / SUPPLY:.2%} of supply"),
        ("Creator has never sold", not creator_sold, "sold" if creator_sold else "no sells on record"),
        ("Launch snipers still hold < 5% of supply", sniper_now <= 0.05 * SUPPLY, f"launch wallets now hold {sniper_now / SUPPLY:.2%}"),
        ("Top-10 holders own < 30% of supply", top10 <= 0.30 * SUPPLY, f"{top10 / SUPPLY:.2%}"),
        ("No instant (< 1 min) self-funded graduation", not (migrated and migrated - t0 < 60),
         "not graduated" if not migrated else f"graduated after {(migrated - t0) / 60:.1f} min"),
        ("Not in mayhem mode (supply can't be changed)", not create["mayhem"], "mayhem" if create["mayhem"] else "standard curve"),
    ]
    return {
        "mint": mint, "name": create["name"], "symbol": create["symbol"], "creator": create["creator"],
        "created": t0, "reward_mode": "holders (cashback)" if create["cashback"] else "creator",
        "quote": "SOL" if create["quote"].startswith("1111") else create["quote"], "graduated": migrated,
        "window_truncated": truncated, "curve_progress": min(1.0, r_sol / GRAD_REAL_SOL), "real_sol": r_sol,
        "price_sol": price, "mcap_sol": price * SUPPLY, "mcap_usd": price * SUPPLY * usd if usd else None,
        "trades": len(trades), "outside_wallets": len({t["trader"] for t in ext}),
        "volume_sol": sum(t["sol"] for t in trades), "creator_fees_sol": fees, "holders": len(holders),
        "creator_holding_pct": creator_now / SUPPLY, "top10_pct": top10 / SUPPLY,
        "top_holders": [{"wallet": w, "pct": v / SUPPLY, "tag": tag(w)} for w, v in top[:10]],
        "flags": [{"check": c, "ok": bool(ok), "detail": d} for c, ok, d in flags],
        "recent_trades": [{"time": t["time"], "side": "buy" if t["is_buy"] else "sell", "sol": t["sol"],
                           "wallet": t["trader"], "tag": tag(t["trader"]), "sig": t["sig"]} for t in trades[::-1][:25]],
    }


def main():
    key = os.environ.get("HELIUS_API_KEY", "").strip()
    if not key:
        sys.exit("HELIUS_API_KEY is not set.")
    cfg = yaml.safe_load(open(os.path.join(HERE, "config.yaml"), encoding="utf-8"))
    snap = snapshot(key, cfg["mint"], cfg.get("lookback_days", 14), cfg.get("max_pages_per_run", 30))
    image = None
    if cfg.get("image") and os.path.exists(os.path.join(HERE, cfg["image"])):
        image = "data:image/png;base64," + base64.b64encode(open(os.path.join(HERE, cfg["image"]), "rb").read()).decode()
    data = {"generated": int(time.time()), "current": snap, "image": image, "history": []}
    tpl = open(os.path.join(HERE, "template.html"), encoding="utf-8").read()
    html = "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">" \
           "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">" \
           "<style>body{margin:0}img{max-width:100%}</style></head><body>" \
           + tpl.replace("__DATA__", json.dumps(data).replace("</", "<\\/")) + "</body></html>"
    os.makedirs(os.path.join(HERE, "public"), exist_ok=True)
    open(os.path.join(HERE, "public", "index.html"), "w", encoding="utf-8").write(html)
    ok = sum(f["ok"] for f in snap["flags"])
    print(f"built public/index.html for {snap['name']} (${snap['symbol']}): {snap['trades']} trades, "
          f"{snap['holders']} holders, {ok}/{len(snap['flags'])} checks pass"
          + (" [history truncated by max_pages_per_run]" if snap["window_truncated"] else ""))


if __name__ == "__main__":
    main()
