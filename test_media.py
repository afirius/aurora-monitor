"""Mocked tests for native Windows media metadata and thumbnail reads."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import media


class FakeStream:
    def __init__(self, data):
        self.data = data
        self.size = len(data)
        self.closed = False

    def close(self):
        self.closed = True


class FakeThumbnail:
    def __init__(self, stream):
        self.stream = stream
        self.open_count = 0

    async def open_read_async(self):
        self.open_count += 1
        return self.stream


class FakeDataReader:
    instances = []

    def __init__(self, stream):
        self.stream = stream
        self.closed = False
        type(self).instances.append(self)

    async def load_async(self, size):
        return min(size, len(self.stream.data))

    def read_buffer(self, size):
        return bytearray(self.stream.data[:size])

    def close(self):
        self.closed = True


class FakePlaybackStatus:
    PLAYING = "playing"
    PAUSED = "paused"


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.saved_state = (
            media._CACHE,
            media._CACHE_EXPIRES_AT,
            media._THUMBNAIL_CACHE_KEY,
            media._THUMBNAIL_CACHE_BYTES,
            media._THUMBNAIL_ATTEMPT_AT,
        )
        media._CACHE = {}
        media._CACHE_EXPIRES_AT = 0.0
        media._THUMBNAIL_CACHE_KEY = None
        media._THUMBNAIL_CACHE_BYTES = None
        media._THUMBNAIL_ATTEMPT_AT = None
        media._THUMBNAIL_RETRY_SECONDS = 60.0
        FakeDataReader.instances.clear()

    def tearDown(self):
        (
            media._CACHE,
            media._CACHE_EXPIRES_AT,
            media._THUMBNAIL_CACHE_KEY,
            media._THUMBNAIL_CACHE_BYTES,
            media._THUMBNAIL_ATTEMPT_AT,
        ) = self.saved_state

    @staticmethod
    def fake_manager(session):
        class Manager:
            @staticmethod
            async def request_async():
                return SimpleNamespace(get_current_session=lambda: session)

        return Manager

    @staticmethod
    def fake_session(properties, status=FakePlaybackStatus.PLAYING):
        return SimpleNamespace(
            source_app_user_model_id="Example.Player",
            try_get_media_properties_async=lambda: _resolved(properties),
            get_playback_info=lambda: SimpleNamespace(playback_status=status),
        )

    def test_no_session_returns_empty_mapping(self):
        with patch.object(media, "_load_media_types", return_value=(self.fake_manager(None), FakePlaybackStatus)):
            self.assertEqual(asyncio.run(media._read_media_async()), {})

    def test_playing_session_wins_over_paused_current_session(self):
        paused = self.fake_session(SimpleNamespace(
            title="Paused", artist="Artist", album_title="", thumbnail=None),
            status=FakePlaybackStatus.PAUSED)
        playing = self.fake_session(SimpleNamespace(
            title="Playing", artist="Artist", album_title="", thumbnail=None))

        class Manager:
            @staticmethod
            async def request_async():
                return SimpleNamespace(get_current_session=lambda: paused,
                                       get_sessions=lambda: [paused, playing])

        with patch.object(media, "_load_media_types", return_value=(Manager, FakePlaybackStatus)):
            result = asyncio.run(media._read_media_async())
        self.assertEqual(result["title"], "Playing")
        self.assertTrue(result["is_playing"])

    def test_missing_winrt_bindings_returns_empty_mapping(self):
        with patch.object(media, "_load_media_types", side_effect=ImportError("WinRT unavailable")):
            self.assertEqual(asyncio.run(media._read_media_async()), {})

    def test_reads_metadata_playback_state_and_thumbnail_bytes(self):
        image = b"\x89PNG\r\n\x1a\nexample-cover"
        stream = FakeStream(image)
        thumbnail = FakeThumbnail(stream)
        properties = SimpleNamespace(
            title="Aurora",
            artist="Northwind",
            album_title="Night Drive",
            thumbnail=thumbnail,
        )
        session = self.fake_session(properties)
        with (
            patch.object(media, "_load_media_types", return_value=(self.fake_manager(session), FakePlaybackStatus)),
            patch.object(media, "_load_data_reader", return_value=FakeDataReader),
            patch.object(media, "_MAX_THUMBNAIL_BYTES", 2 * 1024 * 1024),
        ):
            result = asyncio.run(media._read_media_async())

        self.assertEqual(result["title"], "Aurora")
        self.assertEqual(result["artist"], "Northwind")
        self.assertEqual(result["album"], "Night Drive")
        self.assertTrue(result["is_playing"])
        self.assertEqual(result["thumbnail_bytes"], image)
        self.assertEqual(thumbnail.open_count, 1)
        self.assertTrue(FakeDataReader.instances[0].closed)

    def test_paused_track_keeps_real_timeline_and_duration(self):
        properties = SimpleNamespace(
            title="Ridin'", artist="Chamillionaire", album_title="The Sound of Revenge",
            thumbnail=None,
        )
        session = self.fake_session(properties, status=FakePlaybackStatus.PAUSED)
        session.get_timeline_properties = lambda: SimpleNamespace(
            start_time=timedelta(seconds=0),
            end_time=timedelta(seconds=235),
            position=timedelta(seconds=83),
            last_updated_time=datetime.now(timezone.utc) - timedelta(minutes=2),
        )
        with patch.object(media, "_load_media_types", return_value=(self.fake_manager(session), FakePlaybackStatus)):
            result = asyncio.run(media._read_media_async())
        self.assertFalse(result["is_playing"])
        self.assertEqual(result["position_seconds"], 83)
        self.assertEqual(result["duration_seconds"], 235)

    def test_same_track_reuses_thumbnail_without_reopening_stream(self):
        image = b"cover-image"
        thumbnail = FakeThumbnail(FakeStream(image))
        properties = SimpleNamespace(
            title="Aurora",
            artist="Northwind",
            album_title="Night Drive",
            thumbnail=thumbnail,
        )
        session = self.fake_session(properties, status=FakePlaybackStatus.PAUSED)
        with (
            patch.object(media, "_load_media_types", return_value=(self.fake_manager(session), FakePlaybackStatus)),
            patch.object(media, "_load_data_reader", return_value=FakeDataReader),
        ):
            first = asyncio.run(media._read_media_async())
            second = asyncio.run(media._read_media_async())

        self.assertFalse(first["is_playing"])
        self.assertFalse(second["is_playing"])
        self.assertEqual(first["thumbnail_bytes"], image)
        self.assertEqual(second["thumbnail_bytes"], image)
        self.assertEqual(thumbnail.open_count, 1)
        self.assertEqual(len(FakeDataReader.instances), 1)

    def test_thumbnail_is_read_immediately_when_system_uptime_is_under_retry_interval(self):
        image = b"cover-image"
        thumbnail = FakeThumbnail(FakeStream(image))
        properties = SimpleNamespace(
            title="Aurora",
            artist="Northwind",
            album_title="Night Drive",
            thumbnail=thumbnail,
        )
        session = self.fake_session(properties)
        with (
            patch.object(media, "_load_media_types", return_value=(self.fake_manager(session), FakePlaybackStatus)),
            patch.object(media, "_load_data_reader", return_value=FakeDataReader),
            patch.object(media.time, "monotonic", return_value=5.0),
        ):
            result = asyncio.run(media._read_media_async())

        self.assertEqual(result["thumbnail_bytes"], image)
        self.assertEqual(thumbnail.open_count, 1)

    def test_oversized_thumbnail_is_skipped(self):
        thumbnail = FakeThumbnail(FakeStream(b"x" * 32))
        properties = SimpleNamespace(
            title="Aurora",
            artist="Northwind",
            album_title="Night Drive",
            thumbnail=thumbnail,
        )
        session = self.fake_session(properties)
        with (
            patch.object(media, "_load_media_types", return_value=(self.fake_manager(session), FakePlaybackStatus)),
            patch.object(media, "_load_data_reader", return_value=FakeDataReader),
            patch.object(media, "_MAX_THUMBNAIL_BYTES", 16),
        ):
            result = asyncio.run(media._read_media_async())

        self.assertNotIn("thumbnail_bytes", result)
        self.assertEqual(thumbnail.open_count, 1)
        self.assertEqual(FakeDataReader.instances, [])

    def test_manager_timeout_returns_empty_mapping(self):
        class SlowManager:
            @staticmethod
            async def request_async():
                await asyncio.sleep(0.1)

        with (
            patch.object(media, "_load_media_types", return_value=(SlowManager, FakePlaybackStatus)),
            patch.object(media, "_READ_TIMEOUT_SECONDS", 0.01),
        ):
            self.assertEqual(asyncio.run(media._read_media_async()), {})


async def _resolved(value):
    return value


if __name__ == "__main__":
    unittest.main()
