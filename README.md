# Glass Box Coin

A live, public, on-chain transparency page for one pump.fun coin: holders, trades, the creator's
position and a red-flag checklist. Rebuilt every ~10 minutes by a GitHub Action and served by GitHub Pages.

- **Switch coins:** edit `mint` (and `image`) in `config.yaml` and commit. The page rebuilds on push.
- **Refresh now:** Actions tab → "Refresh dashboard" → Run workflow.
- **Secret:** `HELIUS_API_KEY` is stored as a repository secret and never appears in the page or code.
- **Local test:** `pip install -r requirements.txt`, set `HELIUS_API_KEY`, run `python build.py`, open `public/index.html`.

## Momentum lab (`/lab/`)

A public, pre-registered paper-trading test of one memecoin momentum rule (M1), $1,000 paper account
from 2026-09-28. No real money, not advice.

- `lab/m1.py` pulls new daily candles and funding from Binance's free archive (data.binance.vision),
  appends each new Monday's positions to `lab/positions.csv` (never rewritten: its commit history is the
  timestamped record), and writes `lab/equity.csv`. Run by the "Momentum lab" workflow, which the
  Worker's cron starts at 01:20 and 13:20 UTC.
- `lab/render.py` builds `public/lab/index.html` from those committed files (stdlib only) inside the
  10-minute "Refresh dashboard" job.
- `lab/universe.yaml` is the frozen coin list. Nothing about the rule is re-tuned.
