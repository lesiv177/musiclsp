import unittest
from datetime import date

from core.release_radar import is_recent_release


class ReleaseRadarDateTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 11, 7)

    def test_accepts_release_inside_recent_window(self):
        self.assertTrue(is_recent_release(
            {"release_date": "2026-10-15T12:00:00Z"}, days=90, today=self.today
        ))

    def test_rejects_old_missing_invalid_and_future_dates(self):
        for album in (
            {"release_date": "2026-01-01"},
            {},
            {"release_date": "not-a-date"},
            {"release_date": "2026-12-01"},
        ):
            with self.subTest(album=album):
                self.assertFalse(is_recent_release(album, days=90, today=self.today))


if __name__ == "__main__":
    unittest.main()