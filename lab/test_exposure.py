import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

import m1
import m1_exposure as exposure


class ExposureTest(unittest.TestCase):
    week = pd.Timestamp(exposure.START, tz="UTC")

    def test_volatility_excludes_monday_and_later_returns(self):
        dates = pd.date_range(self.week - pd.Timedelta(days=28), periods=30)
        gross = pd.Series([-0.03, 0.03] * 15, index=dates)
        volatility, scale = exposure.scale_for(self.week, gross)
        gross.loc[self.week:] = 1.0
        self.assertEqual(exposure.scale_for(self.week, gross), (volatility, scale))
        self.assertAlmostEqual(scale, 0.20 / (gross.iloc[:28].std() * 365**0.5))
        self.assertEqual(exposure.scale_for(self.week, gross.drop(dates[0])), (None, 0.0))
        self.assertEqual(exposure.scale_for(self.week, gross * 0), (0.0, 1.0))

    def test_missed_publication_window_is_skipped(self):
        weeks = [exposure.START]
        closes = pd.date_range(self.week, periods=4)
        scales = pd.DataFrame(columns=["week"])
        gross = pd.Series(0.0, index=closes)
        self.assertFalse(exposure.weekly_scales(weeks, closes, scales, gross, self.week + pd.Timedelta(hours=23)))
        self.assertFalse(exposure.weekly_scales(weeks, closes, scales, gross, self.week + pd.Timedelta(days=3)))

    def test_comparisons_use_engine_costs_and_replay_frozen_scales(self):
        closes_idx = pd.date_range(self.week, periods=3)
        closes = pd.DataFrame({"X": [1.0, 1.1, 1.0]}, index=closes_idx)
        original_book = pd.DataFrame(1.0, index=closes_idx, columns=closes.columns)
        rates = pd.DataFrame({"X": [0.0, 0.01, 0.0]}, index=closes_idx)
        gross_idx = pd.date_range(end=self.week - pd.Timedelta(days=1), periods=28)
        eq = pd.DataFrame({"date": gross_idx.strftime("%Y-%m-%d"), "gross": [-0.03, 0.03] * 14})
        published_at = self.week + pd.Timedelta(days=1, hours=9)

        with TemporaryDirectory() as tmp, patch.object(m1, "HERE", tmp):
            root = Path(tmp)
            pd.DataFrame({"week": [exposure.START]}).to_csv(root / "positions.csv", index=False)
            m1.run_exposure_comparisons(original_book, closes, rates, closes["X"], eq, published_at, offline=True)
            log = root / exposure.LOG
            self.assertFalse(log.exists())
            m1.run_exposure_comparisons(original_book, closes, rates, closes["X"], eq, published_at, offline=False)
            prior = log.read_bytes()
            accounts = {name: root / exposure.FOLDER / name / "equity.csv" for name in exposure.ACCOUNTS}
            half = pd.read_csv(accounts["m1_half"])
            self.assertAlmostEqual(half.iloc[0].net, 0.5 * (0.10 - 0.01 - 0.0025))
            before = accounts["m1_volatility"].read_bytes()
            eq["gross"] = 0.0  # corrected prices change the signal input, not past decisions
            m1.run_exposure_comparisons(original_book, closes, rates, closes["X"], eq, published_at, offline=False)
            m1.run_exposure_comparisons(original_book, closes, rates, closes["X"], eq, published_at, offline=True)
            self.assertEqual(log.read_bytes(), prior)
            self.assertEqual(accounts["m1_volatility"].read_bytes(), before)
