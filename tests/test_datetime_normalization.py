from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.time_utils import normalize_ftp_datetime


class NormalizeFtpDatetimeTests(unittest.TestCase):
    def test_ftp_with_fraction_to_iso(self):
        actual = normalize_ftp_datetime("20260318110227.000")
        self.assertEqual(actual, "2026-03-18T11:02:27+00:00")

    def test_ftp_without_fraction_to_iso(self):
        actual = normalize_ftp_datetime("20260318110227")
        self.assertEqual(actual, "2026-03-18T11:02:27+00:00")

    def test_datetime_input_to_iso(self):
        source = datetime(2026, 3, 18, 11, 2, 27, tzinfo=timezone.utc)
        actual = normalize_ftp_datetime(source)
        self.assertEqual(actual, "2026-03-18T11:02:27+00:00")

    def test_iso_string_roundtrip(self):
        source = "2026-03-20T07:03:29.322626+00:00"
        actual = normalize_ftp_datetime(source)
        self.assertEqual(actual, source)

    def test_invalid_or_none_falls_back_to_now(self):
        for value in (None, "not-a-date"):
            actual = normalize_ftp_datetime(value)
            self.assertIsNotNone(datetime.fromisoformat(actual))


if __name__ == "__main__":
    unittest.main()
