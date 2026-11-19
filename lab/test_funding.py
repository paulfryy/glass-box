import unittest

import pandas as pd

import funding
import m1


class FundingTest(unittest.TestCase):
    def test_late_archive_is_retried_after_newer_month_exists(self):
        dates = pd.date_range("2026-10-31", "2026-11-01", tz="UTC")
        closes = pd.DataFrame(1.0, index=dates, columns=["A", "B"])
        rates = pd.DataFrame({"A": [0, 0], "B": [float("nan"), 0]}, index=dates)
        calls = []

        def fetch(symbol, month):
            calls.append((symbol, month))
            return pd.Series(0.001, index=dates[:1])

        repaired = funding.refresh(rates, closes, dates[0], fetch, pd.Timestamp("2026-12-01", tz="UTC"))
        self.assertEqual(calls, [("B", "2026-10")])
        self.assertEqual(repaired.at[dates[0], "B"], 0.001)
        self.assertEqual(repaired.at[dates[1], "B"], 0)

    def test_provisional_funding_updates_equity_after_backfill(self):
        dates = pd.date_range("2026-10-31", "2026-11-02", tz="UTC")
        closes = pd.DataFrame(1.0, index=dates, columns=["A"])
        positions = -closes
        held = positions.shift(1).iloc[1:]
        rates = pd.DataFrame({"A": [0, 0, float("nan")]}, index=dates)
        incomplete = funding.coverage(held, rates)
        self.assertEqual(incomplete, {"funding_through": "2026-11-01", "funding_missing_contract_days": "1"})
        self.assertEqual(m1.run(positions, closes, rates, 0, 0).net.sum(), 0)
        rates.loc[dates[-1], "A"] = 0.01
        self.assertEqual(funding.coverage(held, rates)["funding_missing_contract_days"], "0")
        self.assertAlmostEqual(m1.run(positions, closes, rates, 0, 0).net.sum(), 0.01)
