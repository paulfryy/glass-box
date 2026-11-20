import json
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import archive_timing


class ArchiveTimingTest(unittest.TestCase):
    def test_observation_keeps_first_seen_and_stops_after_completion(self):
        day = date(2026, 11, 23)
        now = datetime(2026, 11, 24, 10, tzinfo=UTC)
        first = {"first_seen_utc": now.isoformat(), "last_modified_utc": now.isoformat()}
        later = {"first_seen_utc": now.replace(minute=15).isoformat(), "last_modified_utc": now.isoformat()}
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            closes = root / "closes.csv"
            closes.write_text("time,AAUSDT,BBUSDT,CCUSDT\n2026-11-22,1,2,\n2026-11-23,1,2,3\n", encoding="utf-8")
            directory = root / "days"
            with patch.object(archive_timing, "probe", side_effect=lambda day, symbol:
                              (symbol, first if symbol == "AAUSDT" else None, None)):
                archive_timing.observe(directory, closes, day, now)
            with patch.object(archive_timing, "probe", return_value=("BBUSDT", later, None)) as probe:
                archive_timing.observe(directory, closes, day, now.replace(minute=15))
                archive_timing.observe(directory, closes, day, now.replace(minute=30))
                probe.assert_called_once_with(day, "BBUSDT")

            record = json.loads((directory / f"{day}.json").read_text(encoding="utf-8"))
            self.assertEqual(record["required"], ["AAUSDT", "BBUSDT"])
            self.assertEqual(record["files"]["AAUSDT"], first)
            self.assertEqual(record["files"]["BBUSDT"], later)
            self.assertIn("10.2 hours", archive_timing.summary(root))
