# SPDX-License-Identifier: GPL-3.0-or-later
"""Serial transport for the TURZX 3.5 inch USB screen (revision A protocol).

Protocol details are adapted from mathoudebine/turing-smart-screen-python:
https://github.com/mathoudebine/turing-smart-screen-python/blob/main/library/lcd/lcd_comm_rev_a.py
Copyright (C) 2021 Matthieu Houdebine. The upstream project is GPL-3.0-or-later.

The display consumes RGB565 little-endian pixels after a DISPLAY_BITMAP
rectangle command. Coordinates are inclusive. All device I/O belongs to the
worker thread; producers only replace the one pending PIL image.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import serial
from serial.tools import list_ports
from PIL import Image, ImageChops


_VID = 0x1A86
_PID = 0x5722
_SERIAL_NUMBER = "USB35INCHIPSV2"
_WIDTH = 320
_HEIGHT = 480
_BAUD = 115200
_RESET_DELAY = 5.0
_CHUNK_SIZE = 2560  # Upstream uses display width * 8 bytes per write.
_ORIENTATION = {0: 0, 90: 2, 180: 1, 270: 3}
_DIRTY_TILE = 16
_REGION_COST = 128  # Six wire bytes plus the latency of another bitmap command.
_MAX_REGIONS = 24


class DeviceUnavailable(RuntimeError):
    """The selected USB screen is absent or is not the supported model."""


def _matches_screen(port: Any) -> bool:
    serial_number = str(getattr(port, "serial_number", "") or "").upper()
    return serial_number == _SERIAL_NUMBER or (
        getattr(port, "vid", None) == _VID and getattr(port, "pid", None) == _PID
    )


def _select_port(requested: str) -> str:
    candidates = [port for port in list_ports.comports() if _matches_screen(port)]
    if requested.upper() == "AUTO":
        if not candidates:
            raise DeviceUnavailable("Дисплей TURZX USB35INCHIPSV2 не найден")
        # If several compatible devices exist, prefer the exact serial number.
        candidates.sort(key=lambda p: str(getattr(p, "serial_number", "") or "").upper() != _SERIAL_NUMBER)
        return str(candidates[0].device)
    for port in candidates:
        if str(port.device).casefold() == requested.casefold():
            return str(port.device)
    raise DeviceUnavailable(f"На порту {requested} не найден дисплей TURZX USB35INCHIPSV2")


def _command(code: int, x: int = 0, y: int = 0, ex: int = 0, ey: int = 0) -> bytes:
    """Pack four unsigned ten-bit coordinates and one command byte."""
    if any(not 0 <= value <= 1023 for value in (x, y, ex, ey)):
        raise ValueError("Координата команды дисплея вне допустимого диапазона")
    return bytes((
        x >> 2,
        ((x & 3) << 6) | (y >> 4),
        ((y & 15) << 4) | (ex >> 6),
        ((ex & 63) << 2) | (ey >> 8),
        ey & 255,
        code,
    ))


def _orientation_packet(rotation: int) -> bytes:
    width, height = (_WIDTH, _HEIGHT) if rotation in (0, 180) else (_HEIGHT, _WIDTH)
    return _command(121) + bytes((
        100 + _ORIENTATION[rotation],
        width >> 8, width & 255,
        height >> 8, height & 255,
        0, 0, 0, 0, 0,
    ))


def _brightness_packet(percent: int) -> bytes:
    # Hardware value 0 is brightest and 255 is darkest.
    absolute = int(255 - percent * 255 / 100)
    return _command(110, absolute)


def _rgb565le(image: Image.Image) -> bytes:
    """Serialize row-major RGB pixels as little-endian RGB565."""
    rgb = image.convert("RGB").tobytes()
    result = bytearray((len(rgb) // 3) * 2)
    j = 0
    for i in range(0, len(rgb), 3):
        value = ((rgb[i] & 0xF8) << 8) | ((rgb[i + 1] & 0xFC) << 3) | (rgb[i + 2] >> 3)
        result[j] = value & 255
        result[j + 1] = value >> 8
        j += 2
    return bytes(result)


def _write_all(connection: Any, data: bytes) -> None:
    """Treat a zero or partial serial write as an error rather than losing pixels."""
    offset = 0
    while offset < len(data):
        count = connection.write(data[offset:])
        if count is None or count <= 0:
            raise serial.SerialTimeoutException("Запись в порт дисплея не продвигается")
        offset += count


def _area(box: tuple[int, int, int, int]) -> int:
    return (box[2] - box[0]) * (box[3] - box[1])


def _union(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])


def _overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])


def _dirty_regions(image: Image.Image, previous: Image.Image | None) -> list[tuple[int, int, int, int]]:
    """Group changed 16px tiles, then merge rectangles by estimated wire cost.

    Each rectangle is half-open and the result never overlaps. Grouping only
    marks tiles containing an actual pixel change, while the final cost check
    falls back to a full frame for page or theme changes.
    """
    width, height = image.size
    full = (0, 0, width, height)
    if previous is None or previous.size != image.size:
        return [full]
    diff = ImageChops.difference(previous, image)
    if diff.getbbox() is None:
        return []

    # Crop/getbbox is implemented in Pillow's C core, so scanning the roughly
    # 600 tiles of a landscape frame is inexpensive compared with serial I/O.
    tiles: dict[tuple[int, int], tuple[int, int, int, int]] = {}
    columns = (width + _DIRTY_TILE - 1) // _DIRTY_TILE
    rows = (height + _DIRTY_TILE - 1) // _DIRTY_TILE
    for ty in range(rows):
        y0 = ty * _DIRTY_TILE
        for tx in range(columns):
            x0 = tx * _DIRTY_TILE
            bounds = diff.crop((x0, y0, min(x0 + _DIRTY_TILE, width), min(y0 + _DIRTY_TILE, height))).getbbox()
            if bounds is not None:
                tiles[(tx, ty)] = (x0 + bounds[0], y0 + bounds[1], x0 + bounds[2], y0 + bounds[3])
    if len(tiles) > 8 and len(tiles) > columns * rows * 0.6:
        return [full]

    regions = []
    while tiles:
        origin, box = tiles.popitem()
        stack = [origin]
        while stack:
            tx, ty = stack.pop()
            for neighbor in ((tx - 1, ty), (tx + 1, ty), (tx, ty - 1), (tx, ty + 1)):
                other = tiles.pop(neighbor, None)
                if other is not None:
                    box = _union(box, other)
                    stack.append(neighbor)
        regions.append(box)
    if len(regions) > 96:
        return [full]

    # Merging also resolves the rare case where separate tile components have
    # overlapping bounding rectangles (for example a ring around another).
    while len(regions) > 1:
        best = None
        best_saving = float("-inf")
        must_merge_overlap = False
        for i, left in enumerate(regions):
            for j in range(i + 1, len(regions)):
                right = regions[j]
                combined = _union(left, right)
                saving = 2 * (_area(left) + _area(right) - _area(combined)) + _REGION_COST
                overlap = _overlap(left, right)
                if (overlap and not must_merge_overlap) or (overlap == must_merge_overlap and saving > best_saving):
                    best_saving, best = saving, (i, j, combined)
                    must_merge_overlap = overlap
        if best is None or (not must_merge_overlap and best_saving < 0 and len(regions) <= _MAX_REGIONS):
            break
        i, j, combined = best
        regions[i] = combined
        regions.pop(j)

    if sum(2 * _area(box) + _REGION_COST for box in regions) >= 2 * width * height * 0.85:
        return [full]
    return sorted(regions, key=lambda box: (box[1], box[0]))


def _send_bitmap(connection: Any, image: Image.Image, previous: Image.Image | None) -> tuple[int, int]:
    """Transmit complete rectangles; return wire bytes and region count."""
    regions = _dirty_regions(image, previous)
    # Prepare every payload before entering the device's bitmap-data state.
    prepared = [(box, _rgb565le(image.crop(box))) for box in regions]
    total_bytes = 0
    for (x0, y0, x1, y1), pixels in prepared:
        _write_all(connection, _command(197, x0, y0, x1 - 1, y1 - 1))
        total_bytes += 6 + len(pixels)
        for offset in range(0, len(pixels), _CHUNK_SIZE):
            _write_all(connection, pixels[offset:offset + _CHUNK_SIZE])
    return total_bytes, len(regions)


class DisplayWorker:
    """Latest-frame-only, reconnecting screen transport.

    ``submit`` accepts RGB-compatible PIL images at the configured logical
    screen size. Rotation selects the screen's hardware orientation: 0/180
    require 320x480 frames, 90/270 require 480x320 frames. ``last_frame_ms``
    in ``status`` is the duration of the last completed serial frame transfer.
    """

    def __init__(self, port: str = "AUTO", rotation: int = 90, brightness: int = 70, reset_on_connect: bool = True):
        self._reset_on_connect = reset_on_connect
        self._condition = threading.Condition()
        self._port = self._validate_port(port)
        self._rotation = self._validate_rotation(rotation)
        self._brightness = self._clamp_brightness(brightness)
        self._version = 0
        self._pending: Image.Image | None = None
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._status = {
            "connected": False,
            "port": None,
            "message": "Остановлено",
            "frames": 0,
            "last_frame_ms": None,
            "last_frame_bytes": None,
            "last_frame_regions": None,
        }

    @staticmethod
    def _validate_port(port: str) -> str:
        if not isinstance(port, str) or not port.strip():
            raise ValueError("Укажите AUTO или имя последовательного порта")
        return port.strip()

    @staticmethod
    def _validate_rotation(rotation: int) -> int:
        if rotation not in _ORIENTATION:
            raise ValueError("Поворот должен быть 0, 90, 180 или 270 градусов")
        return rotation

    @staticmethod
    def _clamp_brightness(brightness: int) -> int:
        try:
            return max(0, min(100, int(brightness)))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Яркость должна быть числом") from exc

    @property
    def status(self) -> dict[str, Any]:
        with self._condition:
            return self._status.copy()

    def start(self) -> None:
        with self._condition:
            self._stopping = False
            if self._thread is not None and self._thread.is_alive():
                self._status["message"] = "Подключено" if self._status["connected"] else "Подключение"
                self._condition.notify_all()
                return
            self._start_thread_locked()

    def _start_thread_locked(self) -> None:
        """Start exactly one worker while holding the condition lock."""
        self._status.update(connected=False, port=None, message="Подключение")
        self._thread = threading.Thread(target=self._run, name="TURZX display", daemon=True)
        self._thread.start()

    def submit(self, image: Image.Image) -> None:
        if not isinstance(image, Image.Image):
            raise TypeError("Для отправки кадра требуется изображение PIL")
        with self._condition:
            expected = (_WIDTH, _HEIGHT) if self._rotation in (0, 180) else (_HEIGHT, _WIDTH)
            if image.size != expected:
                raise ValueError(
                    f"При повороте {self._rotation} размер кадра должен быть {expected[0]}x{expected[1]}"
                )
            # Keep an owned snapshot; callers may render into their image again.
            self._pending = image.convert("RGB")
            self._condition.notify_all()

    def configure(self, port: str, rotation: int, brightness: int) -> None:
        port = self._validate_port(port)
        rotation = self._validate_rotation(rotation)
        brightness = self._clamp_brightness(brightness)
        with self._condition:
            if (port, rotation, brightness) == (self._port, self._rotation, self._brightness):
                return
            if rotation != self._rotation:
                self._pending = None
            self._port, self._rotation, self._brightness = port, rotation, brightness
            self._version += 1
            self._condition.notify_all()

    def stop(self) -> None:
        """Request shutdown without waiting for a serial call on the UI thread."""
        with self._condition:
            self._stopping = True
            self._pending = None
            if self._thread is None or not self._thread.is_alive():
                self._thread = None
                self._status.update(connected=False, port=None, message="Остановлено")
            else:
                self._status["message"] = "Остановка"
            self._condition.notify_all()

    def wait_closed(self, timeout: float = 10.0) -> None:
        """Let the in-flight bitmap finish before the process exits."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _set_status(self, **changes: Any) -> None:
        with self._condition:
            self._status.update(changes)

    def _run(self) -> None:
        connection = None
        connected_port = None
        rotation_sent = None
        brightness_sent = None
        previous = None
        backoff = 0.5
        needs_reset = self._reset_on_connect
        logged_frames = 0
        try:
            while True:
                frame = None
                with self._condition:
                    if self._stopping:
                        break
                    requested = self._port
                    rotation = self._rotation
                    brightness = self._brightness
                    version = self._version

                try:
                    if connection is not None and requested != connected_port[0]:
                        connection.close()
                        connection = None
                        previous = None

                    if connection is None:
                        device = _select_port(requested)
                        connection = serial.Serial(
                            device, _BAUD, timeout=1, write_timeout=1, rtscts=True
                        )
                        connected_port = (requested, device)
                        if needs_reset:
                            self._set_status(connected=False, port=device, message="Восстановление дисплея")
                            _write_all(connection, _command(101))
                            connection.close()
                            connection = None
                            needs_reset = False
                            logging.info("USB: отправлен сброс контроллера %s", device)
                            with self._condition:
                                self._condition.wait_for(lambda: self._stopping, timeout=_RESET_DELAY)
                            continue
                        # HELLO is harmless for original revision A screens,
                        # which simply return no response.
                        _write_all(connection, bytes((69,)) * 6)
                        reply = connection.read(6)
                        if reply in (bytes((2,)) * 6, bytes((3,)) * 6):
                            raise DeviceUnavailable("Подключённый USB-дисплей не является моделью 3,5 дюйма")
                        connection.reset_input_buffer()
                        # Opening the port and setting brightness do not wake
                        # a panel left in SCREEN_OFF by the previous client.
                        _write_all(connection, _command(109))
                        rotation_sent = None
                        brightness_sent = None
                        previous = None
                        backoff = 0.5
                        logged_frames = 0

                    if rotation != rotation_sent:
                        _write_all(connection, _orientation_packet(rotation))
                        rotation_sent = rotation
                        previous = None
                    if brightness != brightness_sent:
                        _write_all(connection, _brightness_packet(brightness))
                        brightness_sent = brightness
                    self._set_status(connected=True, port=connected_port[1], message="Подключено")

                    with self._condition:
                        if self._stopping:
                            break
                        if version != self._version:
                            continue
                        frame = self._pending
                        self._pending = None

                    if frame is not None:
                        expected = (_WIDTH, _HEIGHT) if rotation in (0, 180) else (_HEIGHT, _WIDTH)
                        if frame.size != expected:
                            continue
                        started = time.monotonic()
                        frame_bytes, region_count = _send_bitmap(connection, frame, previous)
                        if frame_bytes:
                            connection.flush()
                        previous = frame
                        duration = round((time.monotonic() - started) * 1000)
                        if frame_bytes:
                            if logged_frames < 5:
                                logging.info("USB: кадр %s на %s: %s областей, %s байт, %s мс",
                                             logged_frames + 1, connected_port[1], region_count, frame_bytes, duration)
                                logged_frames += 1
                            with self._condition:
                                self._status["frames"] += 1
                                self._status["last_frame_ms"] = duration
                                self._status["last_frame_bytes"] = frame_bytes
                                self._status["last_frame_regions"] = region_count
                        continue

                    with self._condition:
                        self._condition.wait_for(
                            lambda: self._stopping or self._version != version or self._pending is not None,
                            timeout=1.0,
                        )
                    # An unplugged but idle screen should not remain "connected".
                    if connection is not None and not any(
                        str(p.device).casefold() == connected_port[1].casefold() and _matches_screen(p)
                        for p in list_ports.comports()
                    ):
                        raise DeviceUnavailable("USB-дисплей отключён")

                except Exception as exc:
                    # A failed transfer leaves the screen contents uncertain.
                    # Preserve the newest submitted image, or retry the frame
                    # that was in flight once the device reconnects.
                    with self._condition:
                        if self._pending is None:
                            self._pending = frame if frame is not None else previous
                    if connection is not None:
                        try:
                            connection.close()
                        except Exception:
                            pass
                    connection = None
                    needs_reset = self._reset_on_connect
                    connected_port = None
                    rotation_sent = None
                    brightness_sent = None
                    previous = None
                    if isinstance(exc, DeviceUnavailable):
                        message = str(exc)
                    elif isinstance(exc, serial.SerialTimeoutException):
                        message = "Превышено время записи в дисплей; повторное подключение"
                    elif isinstance(exc, (serial.SerialException, OSError)):
                        message = "Ошибка связи с дисплеем; повторное подключение"
                    else:
                        message = "Ошибка обработки кадра; повторное подключение"
                    self._set_status(connected=False, port=None, message=message)
                    with self._condition:
                        if self._stopping:
                            break
                        self._condition.wait_for(
                            lambda: self._stopping or self._version != version,
                            timeout=backoff,
                        )
                    backoff = min(backoff * 2, 5.0)
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
            with self._condition:
                self._thread = None
                if self._stopping:
                    self._status.update(connected=False, port=None, message="Остановлено")
                else:
                    # start() may have raced with shutdown after the old worker
                    # committed to exit. Its serial port is closed above, so a
                    # successor can now start without concurrent device access.
                    self._start_thread_locked()
