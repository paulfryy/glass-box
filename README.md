# Glass Box Coin

A live, public, on-chain transparency page for one pump.fun coin: holders, trades, the creator's
position and a red-flag checklist. Rebuilt every ~10 minutes by a GitHub Action and served by GitHub Pages.

- **Switch coins:** edit `mint` (and `image`) in `config.yaml` and commit. The page rebuilds on push.
- **Refresh now:** Actions tab → "Refresh dashboard" → Run workflow.
- **Secret:** `HELIUS_API_KEY` is stored as a repository secret and never appears in the page or code.
- **Local test:** `pip install -r requirements.txt`, set `HELIUS_API_KEY`, run `python build.py`, open `public/index.html`.
