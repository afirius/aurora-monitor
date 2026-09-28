"""Read Windows now-playing metadata and thumbnail bytes through WinRT."""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
import threading
import time
from typing import Any


_CACHE_SECONDS = 2.0
_READ_TIMEOUT_SECONDS = 4.0
_THUMBNAIL_TIMEOUT_SECONDS = 1.5
_THUMBNAIL_RETRY_SECONDS = 60.0
_MAX_THUMBNAIL_BYTES = 2 * 1024 * 1024

_CACHE_LOCK = threading.Lock()
_READ_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {}
_CACHE_EXPIRES_AT = 0.0

# Kept separately from the short metadata cache: the same track is polled every
# five seconds, but its album art only needs to be opened and read once.
_THUMBNAIL_CACHE_KEY: tuple[str, str, str, str] | None = None
_THUMBNAIL_CACHE_BYTES: bytes | None = None
_THUMBNAIL_ATTEMPT_AT: float | None = None


def _seconds(value: Any) -> float | None:
    try:
        seconds = float(value.total_seconds())
        return seconds if 0 <= seconds <= 7 * 24 * 3600 else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def _load_media_types():
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus,
    )

    return (
        GlobalSystemMediaTransportControlsSessionManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus,
    )


async def _read_media_once() -> dict[str, Any]:
    global _THUMBNAIL_CACHE_KEY, _THUMBNAIL_CACHE_BYTES, _THUMBNAIL_ATTEMPT_AT

    manager_type, playback_status_type = _load_media_types()
    manager = await manager_type.request_async()
    session = manager.get_current_session()
    try:
        candidates = list(manager.get_sessions())
    except Exception:
        # Some WinRT bindings may expose only the current session.
        candidates = []
    if session is not None:
        candidates.insert(0, session)
    for candidate in candidates:
        try:
            playback = candidate.get_playback_info()
            if playback is not None and playback.playback_status == playback_status_type.PLAYING:
                session = candidate
                break
        except Exception:
            continue
    if session is None:
        _THUMBNAIL_CACHE_KEY = None
        _THUMBNAIL_CACHE_BYTES = None
        _THUMBNAIL_ATTEMPT_AT = None
        return {}

    properties = await session.try_get_media_properties_async()
    if properties is None:
        _THUMBNAIL_CACHE_KEY = None
        _THUMBNAIL_CACHE_BYTES = None
        _THUMBNAIL_ATTEMPT_AT = None
        return {}

    def clean(value: Any) -> str:
        return str(value).strip() if value is not None else ""

    title = clean(properties.title)
    artist = clean(properties.artist)
    album = clean(properties.album_title)
    if not any((title, artist, album)):
        return {}

    try:
        source_app = clean(session.source_app_user_model_id)
    except Exception:
        source_app = ""
    track_key = (source_app, title, artist, album)

    try:
        playback_info = session.get_playback_info()
        status = playback_info.playback_status if playback_info is not None else None
        is_playing = (
            status == playback_status_type.PLAYING
            if status is not None
            else None
        )
    except Exception:
        is_playing = None

    result: dict[str, Any] = {
        "title": title,
        "artist": artist,
        "album": album,
        "is_playing": is_playing,
    }

    try:
        timeline = session.get_timeline_properties()
        start = _seconds(timeline.start_time)
        end = _seconds(timeline.end_time)
        position = _seconds(timeline.position)
        if start is not None and end is not None and end > start and position is not None:
            duration = end - start
            position = max(0.0, min(duration, position - start))
            updated = getattr(timeline, "last_updated_time", None)
            if is_playing is True and isinstance(updated, datetime):
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - updated).total_seconds()
                if 0 <= age <= 3600:
                    position = min(duration, position + age)
            result.update(position_seconds=position,
                          duration_seconds=duration,
                          position_sampled_at=time.monotonic())
    except Exception:
        # Some players publish metadata but no seekable timeline.
        pass

    thumbnail = getattr(properties, "thumbnail", None)
    now = time.monotonic()
    if track_key != _THUMBNAIL_CACHE_KEY:
        _THUMBNAIL_CACHE_KEY = track_key
        _THUMBNAIL_CACHE_BYTES = None
        _THUMBNAIL_ATTEMPT_AT = None

    if _THUMBNAIL_CACHE_BYTES is None and thumbnail is not None:
        if (
            _THUMBNAIL_ATTEMPT_AT is None
            or now - _THUMBNAIL_ATTEMPT_AT >= _THUMBNAIL_RETRY_SECONDS
        ):
            _THUMBNAIL_ATTEMPT_AT = now
            _THUMBNAIL_CACHE_BYTES = await _read_thumbnail_bytes(thumbnail)

    if _THUMBNAIL_CACHE_BYTES is not None:
        result["thumbnail_bytes"] = _THUMBNAIL_CACHE_BYTES
    return result


async def _read_media_async() -> dict[str, Any]:
    """Read one sample, returning an empty mapping if WinRT is unavailable."""
    try:
        return await asyncio.wait_for(_read_media_once(), timeout=_READ_TIMEOUT_SECONDS)
    except Exception:
        # Media sessions can vanish between metadata calls, and the WinRT
        # namespace packages are optional on machines without Windows support.
        return {}


async def _read_thumbnail_bytes(thumbnail: Any) -> bytes | None:
    """Read bounded image bytes from an album-art stream without saving a file."""
    try:
        return await asyncio.wait_for(
            _read_thumbnail_bytes_unbounded(thumbnail),
            timeout=_THUMBNAIL_TIMEOUT_SECONDS,
        )
    except Exception:
        return None


async def _read_thumbnail_bytes_unbounded(thumbnail: Any) -> bytes | None:
    stream = None
    reader = None
    try:
        data_reader_type = _load_data_reader()
        stream = await thumbnail.open_read_async()
        size = int(stream.size)
        if size <= 0 or size > _MAX_THUMBNAIL_BYTES:
            return None

        reader = data_reader_type(stream)
        loaded = int(await reader.load_async(size))
        if loaded != size:
            return None
        buffer = reader.read_buffer(loaded)
        raw = bytes(memoryview(buffer)[:loaded])
        return raw if len(raw) == size else None
    except Exception:
        return None
    finally:
        try:
            if reader is not None:
                reader.close()
            elif stream is not None:
                stream.close()
        except Exception:
            pass


def _load_data_reader():
    from winrt.windows.storage.streams import DataReader

    return DataReader


def read_media() -> dict[str, Any]:
    """Return current metadata and cached album-art bytes for a worker thread."""
    global _CACHE, _CACHE_EXPIRES_AT
    now = time.monotonic()
    with _CACHE_LOCK:
        if now < _CACHE_EXPIRES_AT:
            return copy.deepcopy(_CACHE)

    if not _READ_LOCK.acquire(blocking=False):
        with _CACHE_LOCK:
            return copy.deepcopy(_CACHE)

    try:
        now = time.monotonic()
        with _CACHE_LOCK:
            if now < _CACHE_EXPIRES_AT:
                return copy.deepcopy(_CACHE)

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            try:
                sample = asyncio.run(_read_media_async())
            except Exception:
                sample = {}
        else:
            # This synchronous entry point is intended for the app's worker thread.
            sample = {}

        if not isinstance(sample, dict):
            sample = {}
        with _CACHE_LOCK:
            _CACHE = copy.deepcopy(sample)
            _CACHE_EXPIRES_AT = time.monotonic() + _CACHE_SECONDS
            return copy.deepcopy(_CACHE)
    finally:
        _READ_LOCK.release()
