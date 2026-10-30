import datetime
import unittest

from services.worker.main import normalize_expiry_timestamp


class PremiumExpiryTimestampTests(unittest.TestCase):
    def test_naive_timestamp_is_preserved(self):
        value = datetime.datetime(2026, 10, 30, 12, 0, 0)
        self.assertEqual(normalize_expiry_timestamp(value), value)

    def test_timezone_aware_timestamp_is_converted_to_utc(self):
        value = datetime.datetime(
            2026, 10, 30, 14, 0, 0,
            tzinfo=datetime.timezone(datetime.timedelta(hours=2)),
        )
        self.assertEqual(
            normalize_expiry_timestamp(value),
            datetime.datetime(2026, 10, 30, 12, 0, 0),
        )

    def test_iso_z_timestamp_is_parsed_as_utc(self):
        self.assertEqual(
            normalize_expiry_timestamp("2026-10-30T12:00:00Z"),
            datetime.datetime(2026, 10, 30, 12, 0, 0),
        )

    def test_invalid_expiry_value_is_rejected(self):
        with self.assertRaises((TypeError, ValueError)):
            normalize_expiry_timestamp("not-a-date")


if __name__ == "__main__":
    unittest.main()
