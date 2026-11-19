# Strategy Lab

Public forward tests of fixed trading rules, each with a $5,000 paper account. The homepage shows daily-close results, data coverage, and review criteria. Former `/lab/` URLs redirect to the corresponding pages.

| Page | Experiment | Published positions |
|---|---|---|
| `/momentum/` | M1 weekly long/short memecoin momentum, started September 28, 2026 | `lab/positions.csv` |
| `/m1-exposure/` | M1 full exposure, half exposure, and a weekly volatility cap planned for November 23, 2026 | `lab/m1-exposure/scales.csv` after the first rebalance |
| `/carry/` | BTC and ETH spot/perpetual cash-and-carry, started October 5, 2026 | `lab/carry/positions.csv` |
| `/seesaw/` | S1-smooth72, started October 5, 2026 | `lab/seesaw/positions/` |
| `/controls/` | Equal-weight memecoins and long-only momentum | `lab/controls/` |

The [forward protocol](lab/forward_protocol.md) defines the carry and Seesaw rules. `lab/proofs/` contains OpenTimestamps proofs for the published candidate positions. The enclosing `pump.pro` workspace holds the original research in `reports/` and the token-launch archive in `launch/`. The former site's source is in [archive/coin-dashboard](archive/coin-dashboard/).

## Run locally

```sh
python -m pip install -r lab/requirements.txt
python -m unittest discover -s lab -p 'test_*.py'
python lab/verify.py
python lab/render.py
python -m http.server 8000 --directory public
```

`verify.py` recomputes the active paper accounts in a temporary copy and checks that saved results and position records replay. Pass `--base <git-revision>` after a refresh to check that older published positions were preserved. See the [M1 exposure protocol](lab/m1_exposure_protocol.md) for the comparison and publication rules.

## Updates and publication

GitHub Actions updates the three candidates, replays the saved accounts, stamps the position files, commits the new data, and calls the Pages workflow. The update runs at 09:20 and 21:20 UTC and can also be run from **Update Strategy Lab** in Actions. Source pushes to `main` rebuild the site. The build reads only saved lab files.

Daily prices and monthly funding come from Binance's archive. Equity is provisional where funding for a held contract-day is missing. The updater retries missing completed months per contract, and late funding revises equity while the published positions stay fixed. Seesaw positions are computed once a day using data available before each simulated hour.

The archived Cloudflare Worker still needs to be retired in Cloudflare if it is deployed. The current workflows require no Helius or Worker credentials.
