# 26 — Protocol: forward (live paper) tests of two near-misses

Written 2026-10-03, before either account starts. SHA-256 in `reports/26_forward_tests_protocol.sha256`.
Both strategies were **chosen because their past results looked promising** (reports 19 and 22–23), so their
past results are not evidence. Only data from the start date on counts. No backdating.

**Start:** both paper accounts begin at the Monday daily close of **2026-10-05** (00:00 UTC Tuesday 2026-10-06),
$5,000 each. **Checkpoints:** week 13 (2027-01-04) write-up; week 26 (2027-04-05) verdict.

## F1. BTC + ETH cash-and-carry (report 18's C3)
- Half the account buys BTC and ETH spot (equal amounts); the other half is margin for an equal short in each
  coin's Binance USD-M perpetual. Price moves cancel; the position collects funding.
- Re-set to equal weights each Monday close; brought back to target at each daily close.
- Costs: spot 0.40% fee + 0.05% slippage, perp 0.05% fee + 0.05% slippage, per unit traded. Funding as published.
- Data: Binance's free archive (daily spot and perp closes; funding, which Binance publishes monthly, so official
  results include funding with up to a month's lag; the page says so).
- **Verdict at week 26:** "still alive" if its net return beats US Treasury bills over the same 26 weeks
  (taken as 4% a year, i.e. +1.96%). It's a cash-like strategy, so cash is the bar.

## F2. S1-smooth72 (report 23's rule, unchanged)
- Each Monday close: the 50 most liquid eligible Binance USD-M perps (14-day median daily volume, ≥ 60 days of
  history), excluding BTC, ETH, XRP, LTC, EOS. Hourly OLS predictions of each coin's next-hour return from those
  five coins' latest hourly returns (720-hour window, refitted daily at 00:00 UTC); hourly target: long the top 20%,
  short the bottom 20%, each side half the account; **held position = average of the last 72 hourly targets**.
- Costs: 0.05% fee + 0.05% slippage per unit traded; funding charged hourly at 1/24 of each day's total.
- Data: Binance's free archive (hourly and daily candles), downloaded daily. Positions are **computed once a day,
  after the fact, by fixed public code** from data available before each hour; they can't be published hour by
  hour. The safeguard is that the rule leaves no choices: each day's positions file is committed and
  Bitcoin-stamped, and anyone can re-run the code to check it.
- **Verdict at week 26:** "still alive" if its net return after taker costs is positive and its daily returns'
  Sharpe is at least 0.5.

## What neither verdict means
Twenty-six weeks is short. "Still alive" means "worth continuing to watch", not "proven". Neither strategy is
tradable from the US as specified (F1's perp leg and all of F2 use offshore Binance perpetuals).
