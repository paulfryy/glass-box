# M1 exposure comparison protocol

The three $5,000 paper accounts are scheduled to start at the November 23, 2026 daily close (00:00 UTC on November 24). All hold the published M1 book using the same prices, fees, slippage and funding as M1. Idle cash earns zero in every account.

| Account | Fraction of M1 weights during an active week |
|---|---|
| Full exposure | 100% |
| Half exposure | 50% |
| Volatility cap | The smaller of 100% or 20% divided by trailing annualized volatility |

Compute trailing volatility from the sample standard deviation of M1’s gross daily portfolio returns over the 28 calendar days through the Sunday close, multiplied by the square root of 365. A complete zero-volatility window uses 100%. A missing observation sets the capped book to cash for the week. Gross returns keep the sizing input independent of later funding backfills.

Publish each multiplier within 48 hours of the Monday close from the saved price archive and the published M1 book. If a window is missed, all three comparison accounts hold cash for that week. The first published decision starts the accounts. The paper execution reference is the Monday daily close, even though archive data arrive after that close. The append-only decision file records when the multiplier was published. Each week is timestamped with OpenTimestamps.

Review after 13 and 26 weeks from the first rebalance. Compare net return, annualized daily Sharpe and max drawdown across the three accounts. The primary comparison is the cap versus constant half exposure on net Sharpe. Full exposure shows the cost of reducing exposure. Interpret the comparison alongside average exposure and trading costs.

Volatility scaling has been studied on large-cap crypto momentum portfolios. This test measures how it behaves on the lab’s memecoin book. [Grobys et al., 2025](https://link.springer.com/article/10.1007/s11408-025-00474-9)
