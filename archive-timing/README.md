# Binance archive timing

UTC candle days starting **2026-11-19**.

The Cloudflare timer dispatches checks every 10 minutes from 6 a.m. through 4:50 p.m. UTC, with catch-up checks in the 10 p.m. UTC hour. Each day tracks contracts with a price on the latest saved M1 close preceding it.

Times below are Eastern. First seen means the first successful poll. The server timestamp is `Last-Modified`, which can also reflect a later correction.

| Candle day (UTC) | Seen | First file seen | Full set seen | Latest server timestamp |
|---|---:|---|---|---|
| 2026-11-19 | 54/54 | Nov 20, 7:10 AM | Nov 20, 7:10 AM (already visible) | Nov 20, 3:30 AM |
| 2026-11-20 | 54/54 | Nov 21, 3:20 AM | Nov 21, 4:40 AM | Nov 21, 4:35 AM |
| 2026-11-21 | 54/54 | Nov 22, 2:50 AM | Nov 22, 4:20 AM | Nov 22, 4:14 AM |
| 2026-11-22 | 54/54 | Nov 23, 3:10 AM | Nov 23, 4:00 AM | Nov 23, 3:57 AM |
| 2026-11-23 | 54/54 | Nov 24, 2:40 AM | Nov 24, 5:20 AM | Nov 24, 5:17 AM |
| 2026-11-24 | 54/54 | Nov 25, 2:41 AM | Nov 25, 3:41 AM | Nov 25, 3:39 AM |
| 2026-11-25 | 47/54 | Nov 26, 2:40 AM | — | Nov 26, 2:48 AM |

**6 of 7** observed days have a complete set.
1 set(s) were already visible when checked. They provide upper bounds and are excluded from the timing median.
For 5 day(s) observed going from incomplete to complete, the median was **9.3 hours** after close (range 8.7–10.3 hours).
For the 1 observed Monday candle day(s), the median was **10.3 hours**.

[Daily observation records](days/)
