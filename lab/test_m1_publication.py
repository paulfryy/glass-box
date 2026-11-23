import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import m1
import m1_execution as execution
import m1_readiness as readiness


class ReadinessTest(unittest.TestCase):
    def test_missing_active_contract_blocks_publication_and_is_not_a_delisting(self):
        monday = readiness.FIRST
        days = pd.date_range(end=monday, periods=3)
        closes = pd.DataFrame({"BTCUSDT": [100, 101, 102], "MEMEUSDT": [1, None, 1.2]}, index=days)
        volume = closes * 1e6
        check = lambda now: readiness.check(closes, volume, now, set())
        self.assertEqual(check(monday)["state"], "awaiting_close")
        result = check(monday + pd.Timedelta(days=1, hours=22))
        self.assertEqual(result["state"], "waiting_for_candles")
        self.assertEqual(result["missing"]["2026-11-22"], ["MEMEUSDT"])
        self.assertEqual(check(monday + pd.Timedelta(days=3))["state"], "skipped")
        closes.iloc[1, 1] = 1.1
        volume.iloc[1, 1] = 2e6
        self.assertEqual(check(monday + pd.Timedelta(days=1, hours=10))["state"], "ready")

    def test_missing_publication_carries_previous_book_until_next_published_week(self):
        first = readiness.FIRST
        dates = pd.date_range(first - readiness.DAY, periods=9)
        target = pd.DataFrame({"MEMEUSDT": [0.2] + [0.7] * 8}, index=dates)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "positions.csv"
            path.write_text("week,symbol,weight\n2026-11-30,MEMEUSDT,0.7\n")
            held = readiness.hold_unpublished(target, path)
        self.assertEqual(held["MEMEUSDT"].tolist(), [0.2] * 8 + [0.7])

    def test_publish_appends_only_the_ready_week(self):
        index = pd.date_range(readiness.FIRST, periods=2, freq="W-MON")
        weights = pd.DataFrame({"MEMEUSDT": [0.3, 0.5]}, index=index)
        prices = pd.DataFrame({"MEMEUSDT": [1.0, 1.2]}, index=index)
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "positions.csv")
            m1.publish(weights, prices, path, "2026-11-24 14:00")
            self.assertFalse(Path(path).exists())
            m1.publish(weights, prices, path, "2026-11-24 14:00", index[0])
            original = Path(path).read_bytes()
            m1.publish(weights, prices, path, "2026-11-25 14:00", index[0])
            self.assertEqual(Path(path).read_bytes(), original)
            self.assertEqual(pd.read_csv(path)["week"].tolist(), ["2026-11-23"])


class ExecutionTest(unittest.TestCase):
    def test_reference_and_later_trade_use_the_same_signed_quantity(self):
        books = {"2026-11-16": {"A": 0.2}, "2026-11-23": {"B": 0.3}}
        orders = execution.weekly_orders(books, "2026-11-23", {"A": "10", "B": "5"})
        self.assertEqual(orders["A"]["quantity"], -100)
        self.assertEqual(orders["B"]["quantity"], 300)
        time = datetime(2026, 11, 24, 10, tzinfo=UTC)
        t = int(time.timestamp() * 1000)
        with patch.object(execution, "archived_trade", return_value=(t + 10, 9.0, "123")):
            sale = execution.match_order("A", orders["A"], time, datetime(2026, 11, 25, 10, tzinfo=UTC))
        self.assertEqual(sale["delay_cost_usd"], 100)
        self.assertEqual(sale["trade_id"], "123")

    def test_fill_search_never_uses_trades_before_publication_or_after_window(self):
        rows = [["id"], ["1", "10", "", "", "", "999"],
                ["3", "12", "", "", "", "1003"], ["2", "11", "", "", "", "1001"],
                ["4", "13", "", "", "", "1100"]]
        self.assertEqual(execution.first_trade(rows, 1000, 1100), (1001, 11.0, "2"))
        self.assertIsNone(execution.first_trade(rows, 1004, 1100))

    def test_capture_freezes_new_week_and_confirmed_publication_time(self):
        header = "week,symbol,weight\n"
        before = header + "2026-11-16,A,0.2\n"
        after = before + "2026-11-23,B,0.3\n"
        observed = datetime(2026, 11, 24, 14, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as folder:
            lab = Path(folder) / "lab"
            (lab / "data").mkdir(parents=True)
            (lab / "positions.csv").write_text(after)
            (lab / "data/closes.csv").write_text("time,A,B\n2026-11-23,10,5\n")
            with patch.object(execution.subprocess, "check_output", return_value=before):
                execution.capture(lab, "abc", observed)
                execution.capture(lab, "def", observed.replace(hour=15))
            paths = sorted((lab / execution.FOLDER / "publications").glob("*.json"))
            self.assertEqual([p.stem for p in paths], ["2026-11-23"])
            record = paths[0].read_text()
            self.assertIn('"commit": "abc"', record)
            self.assertIn('"public_by_utc": "2026-11-24T14:00:00.000+00:00"', record)
