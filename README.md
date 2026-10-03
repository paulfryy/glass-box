# Glass Box Coin

A live, public, on-chain transparency page for one pump.fun coin: holders, trades, the creator's
position and a red-flag checklist. Rebuilt every ~10 minutes by a GitHub Action and served by GitHub Pages.

- **Switch coins:** edit `mint` (and `image`) in `config.yaml` and commit. The page rebuilds on push.
- **Refresh now:** Actions tab → "Refresh dashboard" → Run workflow.
- **Secret:** `HELIUS_API_KEY` is stored as a repository secret and never appears in the page or code.
- **Local test:** `pip install -r requirements.txt`, set `HELIUS_API_KEY`, run `python build.py`, open `public/index.html`.

## Strategy lab (`/lab/`)

Trading strategies tested in public with $5,000 paper accounts from 2026-09-28. No real money, not advice.

- `/lab/` overview and scoreboard; `/lab/momentum/` rule M1 (the candidate); `/lab/controls/` the controls
  ("just buy memecoins" and long-only momentum, at spot costs).
- `lab/m1.py` pulls new daily candles and funding from Binance's free archive (data.binance.vision), appends
  each new Monday's positions (M1: `lab/positions.csv`; controls: `lab/controls/<id>/positions.csv`, never
  rewritten) and writes each paper account's `equity.csv`. `lab/proof.py` stamps M1's weekly positions into
  Bitcoin with OpenTimestamps. Both run in the "Momentum lab" workflow, started by the Worker's cron at 09:20
  and 21:20 UTC (Binance publishes each daily file around 08:20 UTC).
- `lab/render.py` builds the three pages from those committed files (stdlib only) inside the 10-minute
  "Refresh dashboard" job. Shared styles: `lab/common.css`.
- `lab/universe.yaml` is the frozen coin list. Nothing about any rule is re-tuned.

### Forward tests (from the 2026-10-05 close)
Two near-misses run as live paper accounts, rules fixed in `lab/forward_protocol.md` (SHA-256 in
`lab/forward_protocol.sha256`) before they start; no backdating.
- `/lab/carry/` — `lab/carry.py`: BTC + ETH cash-and-carry (half spot long, half perp short), weekly positions.
- `/lab/seesaw/` — `lab/seesaw.py`: S1-smooth72 on a frozen 712-contract universe (`lab/seesaw/universe.json`);
  hourly positions computed daily from Binance's archive, one file per day in `lab/seesaw/positions/`.
Both run in the "Momentum lab" workflow; `lab/proof.py` stamps their positions into Bitcoin (`lab/proofs/carry/`,
`lab/proofs/seesaw/`).
