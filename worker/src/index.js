// Glass Box "Check any coin" API: GET /check?mint=<address>
// Reads a pump.fun coin's public on-chain data via Helius and returns red-flag checks.
// The Helius key is a Worker secret (HELIUS_API_KEY) and never reaches the browser.

const PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
const PUMP_AMM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA";
const EVENT_TAG = [0xe4, 0x45, 0xa5, 0x2e, 0x51, 0xcb, 0x9a, 0x1d];
const D_CREATE = [27, 114, 169, 77, 222, 235, 99, 118];
const D_TRADE = [189, 219, 127, 211, 78, 230, 97, 238];
const D_MIGRATE = [189, 233, 93, 185, 92, 148, 234, 148];
const SUPPLY = 1_000_000_000;
const GRAD_REAL_SOL = 85;
const CACHE_TTL_S = 300;
const RATE_PER_MIN = 30;
const CHECKS_VERSION = "v4";   // bump whenever the checks change, so cached results from older logic are ignored
const ALLOWED_ORIGINS = ["https://glassbox.gripe", "https://www.glassbox.gripe", "http://localhost:8000"];

// ---------- base58 ----------
const B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
const B58_MAP = Object.fromEntries([...B58].map((c, i) => [c, i]));
export function b58decode(s) {
  let bytes = [0];
  for (const ch of s) {
    const v = B58_MAP[ch];
    if (v === undefined) throw new Error("bad base58");
    let carry = v;
    for (let i = 0; i < bytes.length; i++) { carry += bytes[i] * 58; bytes[i] = carry & 0xff; carry >>= 8; }
    while (carry) { bytes.push(carry & 0xff); carry >>= 8; }
  }
  for (const ch of s) { if (ch === "1") bytes.push(0); else break; }
  return Uint8Array.from(bytes.reverse());
}
export function b58encode(u8) {
  let digits = [0];
  for (const byte of u8) {
    let carry = byte;
    for (let i = 0; i < digits.length; i++) { carry += digits[i] << 8; digits[i] = carry % 58; carry = (carry / 58) | 0; }
    while (carry) { digits.push(carry % 58); carry = (carry / 58) | 0; }
  }
  let out = "";
  for (const byte of u8) { if (byte === 0) out += "1"; else break; }
  for (let i = digits.length - 1; i >= 0; i--) out += B58[digits[i]];
  return out;
}

// ---------- borsh-ish readers ----------
const eq8 = (u8, off, d) => d.every((x, i) => u8[off + i] === x);
const pk = (u8, o) => b58encode(u8.slice(o, o + 32));
const u64 = (u8, o) => Number(new DataView(u8.buffer, u8.byteOffset + o, 8).getBigUint64(0, true));
function str(u8, o) {
  const n = new DataView(u8.buffer, u8.byteOffset + o, 4).getUint32(0, true);
  return [new TextDecoder().decode(u8.slice(o + 4, o + 4 + n)), o + 4 + n];
}
export function decodeCreate(p) {
  let o = 0, name, symbol, uri;
  [name, o] = str(p, o); [symbol, o] = str(p, o); [uri, o] = str(p, o);
  const d = { name: name.trim(), symbol: symbol.trim(), uri, mint: pk(p, o), bondingCurve: pk(p, o + 32),
    creatorSigner: pk(p, o + 64), creator: pk(p, o + 96), vTok0: u64(p, o + 136), vSol0: u64(p, o + 144) };
  o += 200;
  d.mayhem = p.length > o ? p[o] === 1 : false;
  d.cashback = p.length > o + 1 ? p[o + 1] === 1 : false;
  d.quote = p.length >= o + 34 ? pk(p, o + 2) : "11111111111111111111111111111111";
  return d;
}
export function decodeTrade(p) {
  return { mint: pk(p, 0), sol: u64(p, 32) / 1e9, tok: u64(p, 40) / 1e6, isBuy: p[48] === 1, trader: pk(p, 49) };
}
export function* pumpEvents(tx) {
  const lw = tx.meta.loadedAddresses || {};
  const keys = [...tx.transaction.message.accountKeys, ...(lw.writable || []), ...(lw.readonly || [])];
  for (const grp of tx.meta.innerInstructions || []) {
    for (const ix of grp.instructions) {
      if (keys[ix.programIdIndex] !== PUMP) continue;
      const b = b58decode(ix.data);
      if (eq8(b, 0, EVENT_TAG)) yield [b.slice(8, 16), b.slice(16)];
    }
  }
}

// ---------- Helius RPC ----------
async function rpc(env, method, params) {
  const r = await fetch(`https://mainnet.helius-rpc.com/?api-key=${env.HELIUS_API_KEY}`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
  });
  const j = await r.json();
  if (j.error) throw new Error(`${method}: ${j.error.message || JSON.stringify(j.error)}`);
  return j.result;
}

// ---------- the check ----------
export async function checkCoin(env, mint) {
  // 1. the coin's first transactions (creation, dev buy, launch-block snipers): 1 call
  const first = await rpc(env, "getTransactionsForAddress", [mint, { transactionDetails: "full", sortOrder: "asc",
    limit: 40, encoding: "json", maxSupportedTransactionVersion: 1, filters: { status: "succeeded" } }]);
  let create = null, createSlot = null, createTime = null, migrated = null;
  const early = [];
  for (const tx of first.data || []) {
    for (const [d, p] of pumpEvents(tx)) {
      if (eq8(d, 0, D_CREATE)) {
        const c = decodeCreate(p);
        if (c.mint === mint) { create = c; createSlot = tx.slot; createTime = tx.blockTime; }
      } else if (eq8(d, 0, D_TRADE)) {
        const t = decodeTrade(p);
        if (t.mint === mint) early.push({ ...t, slot: tx.slot, time: tx.blockTime });
      } else if (eq8(d, 0, D_MIGRATE) && pk(p, 32) === mint) {
        migrated = tx.blockTime;
      }
    }
  }
  if (!create) return { ok: false, error: "This doesn't look like a pump.fun coin (no pump.fun creation found for this address)." };
  const creators = new Set([create.creator, create.creatorSigner]);

  // 2. mint authorities, curve state, largest holders: 3 calls (+1 to classify holders)
  const [mintInfo, curveInfo, largest] = await Promise.all([
    rpc(env, "getAccountInfo", [mint, { encoding: "jsonParsed" }]),
    rpc(env, "getAccountInfo", [create.bondingCurve, { encoding: "base64" }]),
    rpc(env, "getTokenLargestAccounts", [mint]),
  ]);
  const mi = mintInfo?.value?.data?.parsed?.info || {};
  let real = 0, complete = false, vSol = create.vSol0 / 1e9, vTok = create.vTok0 / 1e6;
  if (curveInfo?.value) {
    const b = Uint8Array.from(atob(curveInfo.value.data[0]), c => c.charCodeAt(0));
    vTok = u64(b, 8) / 1e6; vSol = u64(b, 16) / 1e9; real = u64(b, 32) / 1e9; complete = b[48] === 1;
  }
  const accts = (largest?.value || []).slice(0, 20);
  const tokenAccts = accts.length ? await rpc(env, "getMultipleAccounts", [accts.map(a => a.address), { encoding: "jsonParsed" }]) : { value: [] };
  const owners = tokenAccts.value.map(v => v?.data?.parsed?.info?.owner || null);
  const ownerInfo = owners.length ? await rpc(env, "getMultipleAccounts", [owners.map(o => o || PUMP), { encoding: "base64", dataSlice: { offset: 0, length: 0 } }]) : { value: [] };
  const holders = [];
  accts.forEach((a, i) => {
    const owner = owners[i];
    const prog = ownerInfo.value[i]?.owner;
    const isCurve = owner === create.bondingCurve || prog === PUMP || prog === PUMP_AMM;
    holders.push({ wallet: owner, pct: Number(a.uiAmountString || a.uiAmount || 0) / SUPPLY,
      tag: isCurve ? (complete ? "liquidity pool" : "bonding curve") : creators.has(owner) ? "creator" : "" });
  });

  // 3. derived signals
  const devBuy = early.filter(t => creators.has(t.trader) && t.isBuy && t.slot === createSlot).reduce((s, t) => s + t.tok, 0);
  // launch snipers: non-creator buys within 2 s of creation (same window as the dashboard)
  const atLaunch = t => t.isBuy && !creators.has(t.trader) && t.time - createTime <= 2;
  const sniperWallets = [...new Set(early.filter(atLaunch).map(t => t.trader))];
  const sniperBought = early.filter(atLaunch).reduce((s, t) => s + t.tok, 0);
  const creatorNow = holders.filter(h => creators.has(h.wallet)).reduce((s, h) => s + h.pct, 0);
  let creatorBalance = creatorNow * SUPPLY;
  if (!creatorNow) {   // creator may be outside the top 20: ask directly (1 call)
    const r = await rpc(env, "getTokenAccountsByOwner", [create.creator, { mint }, { encoding: "jsonParsed" }]);
    creatorBalance = (r?.value || []).reduce((s, a) => s + Number(a.account.data.parsed.info.tokenAmount.uiAmountString || 0), 0);
  }
  const wallets = holders.filter(h => h.wallet && h.tag !== "bonding curve" && h.tag !== "liquidity pool");
  const top10 = wallets.slice(0, 10).reduce((s, h) => s + h.pct, 0);
  const sniperHeldNow = wallets.filter(h => sniperWallets.includes(h.wallet)).reduce((s, h) => s + h.pct, 0);
  const soldDown = devBuy > 0 && creatorBalance < devBuy * 0.99;
  const instantGrad = migrated && createTime && migrated - createTime < 60;
  const f = (check, ok, detail, severity, why) => ({ check, ok, detail, severity, why });
  const flags = [
    f("Mint authority revoked (no one can print more tokens)", !mi.mintAuthority, mi.mintAuthority ? "still active" : "revoked", "critical",
      "An active mint authority can create unlimited new tokens and dilute every holder."),
    f("Freeze authority revoked (your tokens can't be frozen)", !mi.freezeAuthority, mi.freezeAuthority ? "still active" : "revoked", "critical",
      "An active freeze authority can lock holders' tokens so they can't sell."),
    f("Not in mayhem mode (supply can't change)", !create.mayhem, create.mayhem ? "mayhem mode" : "standard curve", "high",
      "Mayhem-mode coins let the protocol change the curve and supply for 24 hours."),
    // Thresholds from 12 days of pump.fun launches: the median outside buyer does about the same
    // below ~3%, loses noticeably more at 3-10% (~-4% to -7%) and ~3-4x more at 10%+ (~-11%).
    f("Creator's launch buy is under 3% of supply", devBuy < 0.03 * SUPPLY, `${(devBuy / SUPPLY * 100).toFixed(2)}% of supply`,
      devBuy >= 0.10 * SUPPLY ? "high" : "medium",
      devBuy >= 0.10 * SUPPLY
        ? "Launch buys of 10% or more came with outside buyers losing about 3-4x more than on small-buy launches in our data."
        : "Launch buys of 3-10% came with noticeably worse results for outside buyers in our data."),
    f("Creator still holds their launch buy", !soldDown, devBuy ? (soldDown ? `holds ${(creatorBalance / devBuy * 100).toFixed(0)}% of what they bought` : "still holding") : "no launch buy", "high",
      "Creators selling early is the most direct sign a coin is being dumped. (Tokens moved to another wallet also count as sold here.)"),
    f("Launch buyers took < 20% of supply", sniperBought < 0.20 * SUPPLY, `${sniperWallets.length} wallet(s) bought ${(sniperBought / SUPPLY * 100).toFixed(2)}% within 2 s of launch`, "high",
      "Many wallets buying a big share in the first seconds is the signature of bundled or sniper wallets, which usually sell into later buyers."),
    f("Launch snipers still hold < 5% of supply", sniperHeldNow < 0.05, `launch wallets among the top holders now hold ${(sniperHeldNow * 100).toFixed(2)}%`, "medium",
      "Supply still held by launch snipers can be dumped on buyers at any moment."),
    f("Top-10 wallets own < 30% of supply", top10 < 0.30, `${(top10 * 100).toFixed(2)}%`, "medium",
      "Concentrated holdings mean a few wallets can crash the price."),
    f("No instant (< 1 min) self-funded graduation", !instantGrad, migrated ? `graduated after ${((migrated - createTime) / 60).toFixed(1)} min` : (complete ? "graduated" : "not graduated"), "high",
      "Filling the whole curve within a minute usually means the creator bought it out, not real demand."),
  ];
  const fails = flags.filter(x => !x.ok);
  const highFails = fails.filter(x => x.severity === "high").length;
  const verdict = fails.some(x => x.severity === "critical") || highFails >= 2 ? "danger" : highFails ? "caution"
    : fails.length ? "mixed" : "clean";
  return {
    ok: true, mint, name: create.name, symbol: create.symbol, creator: create.creator, created: createTime,
    rewardMode: create.cashback ? "holders (cashback)" : "creator", graduated: complete,
    curveProgress: complete ? 1 : Math.min(1, real / GRAD_REAL_SOL), realSol: real, mcapSol: (vSol / vTok) * SUPPLY,
    flags, verdict, failCount: fails.length, topHolders: holders.slice(0, 10), checkedAt: Math.floor(Date.now() / 1000),
    note: "Warning signs, not guarantees. A clean result doesn't make a coin safe, and a flag doesn't prove a scam.",
  };
}


// ---------- clock: rebuild the coin dashboard every 10 minutes ----------
// GitHub's own scheduler skips frequent jobs, so a Cloudflare Cron Trigger starts the
// GitHub Action instead. GITHUB_TOKEN is a fine-grained token limited to Actions on this repo.
async function triggerRebuild(env, workflow = "update.yml") {
  const r = await fetch(`https://api.github.com/repos/paulfryy/glass-box/actions/workflows/${workflow}/dispatches`, {
    method: "POST",
    headers: { Authorization: "Bearer " + env.GITHUB_TOKEN, Accept: "application/vnd.github+json",
      "User-Agent": "glassbox-api", "X-GitHub-Api-Version": "2022-11-28" },
    body: JSON.stringify({ ref: "main" }),
  });
  if (r.status !== 204) console.log(workflow, "trigger failed", r.status, (await r.text()).slice(0, 200));
}

// Momentum lab: Binance publishes each day's candle shortly after 00:00 UTC; check twice a day.
const LAB_CRON = "20 1,13 * * *";

// ---------- HTTP ----------
const hits = new Map();   // best-effort per-isolate rate limit
function limited(ip) {
  const now = Date.now(), win = hits.get(ip)?.filter(t => now - t < 60_000) || [];
  win.push(now); hits.set(ip, win);
  return win.length > RATE_PER_MIN;
}
function cors(origin) {
  return { "Access-Control-Allow-Origin": ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0],
    "Access-Control-Allow-Methods": "GET, OPTIONS", "Vary": "Origin" };
}
const json = (obj, status, origin, extra = {}) => new Response(JSON.stringify(obj), {
  status, headers: { "content-type": "application/json", ...cors(origin), ...extra } });

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(triggerRebuild(env, event.cron === LAB_CRON ? "lab.yml" : "update.yml"));
  },

  async fetch(request, env, ctx) {
    const url = new URL(request.url), origin = request.headers.get("Origin") || "";
    if (request.method === "OPTIONS") return new Response(null, { headers: cors(origin) });
    if (url.pathname !== "/check") return json({ ok: false, error: "Use /check?mint=<coin address>" }, 404, origin);
    const mint = (url.searchParams.get("mint") || "").trim();
    if (!/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(mint)) return json({ ok: false, error: "That isn't a valid Solana address." }, 400, origin);
    const cacheKey = new Request(`https://cache.glassbox/${CHECKS_VERSION}/check/${mint}`);
    const hit = await caches.default.match(cacheKey);
    if (hit) { const body = await hit.json(); return json({ ...body, cached: true }, 200, origin); }
    if (limited(request.headers.get("CF-Connecting-IP") || "?")) return json({ ok: false, error: "Too many checks. Wait a minute and try again." }, 429, origin);
    try {
      const result = await checkCoin(env, mint);
      if (result.ok) ctx.waitUntil(caches.default.put(cacheKey, new Response(JSON.stringify(result),
        { headers: { "content-type": "application/json", "Cache-Control": `max-age=${CACHE_TTL_S}` } })));
      return json(result, result.ok ? 200 : 404, origin);
    } catch (e) {
      return json({ ok: false, error: "Couldn't read this coin right now. Try again in a moment." , detail: String(e.message || e).slice(0, 200) }, 502, origin);
    }
  },
};
