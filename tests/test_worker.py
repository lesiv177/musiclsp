import datetime
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from services.worker import main as worker
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


class WorkerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_can_reuse_the_bot_database_pool(self):
        with (
            patch.object(worker.db, "init_engine") as init_engine,
            patch.object(worker, "cycle", new_callable=AsyncMock) as cycle,
            patch.object(worker.asyncio, "sleep", new_callable=AsyncMock,
                         side_effect=asyncio.CancelledError),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await worker.run(initialize=False)

        init_engine.assert_not_called()
        cycle.assert_awaited_once()


class ReleaseRadarNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_digest_sends_only_new_recent_releases_and_marks_after_delivery(self):
        follows = [{
            "uid": 42, "artist_id": "artist:1", "artist_name": "Artist One",
            "artist": {"name": "Artist One"}, "lang": "en",
        }]
        albums = [{
            "id": "jam:album-1", "source": "jamendo", "title": "New Album",
            "artist": "Artist One", "release_date": "2026-11-07",
        }]
        with (
            patch.object(worker, "BOT_TOKEN", "test-token"),
            patch.object(worker.db, "artist_following_all", return_value=follows),
            patch.object(worker.P, "search_albums_smart", return_value=albums),
            patch.object(worker, "is_recent_release", return_value=True),
            patch.object(worker.db, "release_radar_unnotified", return_value=[
                worker.release_notification_key(albums[0])
            ]),
            patch.object(worker, "notify", new_callable=AsyncMock, return_value=True) as send,
            patch.object(worker.db, "release_radar_mark_notified") as mark,
        ):
            count = await worker.notify_release_radar()

        self.assertEqual(count, 1)
        self.assertEqual(send.await_count, 1)
        self.assertIn("New releases", send.await_args.args[1])
        mark.assert_called_once_with(42, [worker.release_notification_key(albums[0])])

    async def test_already_notified_release_is_not_sent_again(self):
        follows = [{
            "uid": 42, "artist_id": "artist:1", "artist_name": "Artist One",
            "artist": {"name": "Artist One"}, "lang": "uk",
        }]
        albums = [{
            "id": "jam:album-1", "source": "jamendo", "title": "Old Notification",
            "artist": "Artist One", "release_date": "2026-11-07",
        }]
        with (
            patch.object(worker, "BOT_TOKEN", "test-token"),
            patch.object(worker.db, "artist_following_all", return_value=follows),
            patch.object(worker.P, "search_albums_smart", return_value=albums),
            patch.object(worker, "is_recent_release", return_value=True),
            patch.object(worker.db, "release_radar_unnotified", return_value=[]),
            patch.object(worker, "notify", new_callable=AsyncMock) as send,
            patch.object(worker.db, "release_radar_mark_notified") as mark,
        ):
            count = await worker.notify_release_radar()

        self.assertEqual(count, 0)
        send.assert_not_awaited()
        mark.assert_not_called()

    async def test_same_display_name_does_not_cross_notify_different_mbid(self):
        follows = [
            {"uid": 42, "artist_id": "artist:one", "artist_name": "Artist",
             "artist": {"name": "Artist", "mbid": "11111111-1111-4111-8111-111111111111"}, "lang": "en"},
            {"uid": 43, "artist_id": "artist:two", "artist_name": "Artist",
             "artist": {"name": "Artist", "mbid": "22222222-2222-4222-8222-222222222222"}, "lang": "en"},
        ]
        albums = [{
            "id": "jam:album-1", "source": "jamendo", "title": "New Album",
            "artist": "Artist", "artist_mbid": "11111111-1111-4111-8111-111111111111",
            "release_date": "2026-11-07",
        }]
        release_key = worker.release_notification_key(albums[0])
        with (
            patch.object(worker, "BOT_TOKEN", "test-token"),
            patch.object(worker.db, "artist_following_all", return_value=follows),
            patch.object(worker.P, "search_albums_smart", return_value=albums),
            patch.object(worker, "is_recent_release", return_value=True),
            patch.object(worker.db, "release_radar_unnotified", return_value=[release_key]),
            patch.object(worker, "notify", new_callable=AsyncMock, return_value=True) as send,
            patch.object(worker.db, "release_radar_mark_notified") as mark,
        ):
            count = await worker.notify_release_radar()

        self.assertEqual(count, 1)
        self.assertEqual([call.args[0] for call in send.await_args_list], [42])
        mark.assert_called_once_with(42, [release_key])

    def test_release_notification_key_is_stable_and_source_aware(self):
        album = {"id": "42", "source": "jamendo", "title": "Album"}
        self.assertEqual(worker.release_notification_key(album),
                         worker.release_notification_key(dict(album)))
        self.assertNotEqual(worker.release_notification_key(album),
                            worker.release_notification_key({**album, "source": "audius"}))


if __name__ == "__main__":
    unittest.main()