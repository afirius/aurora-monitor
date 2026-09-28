"""Protocol and worker tests; no physical screen is opened."""

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from PIL import ImageDraw

import display


class FakeSerial:
    instances = []

    def __init__(self, port, baudrate, **options):
        self.port = port
        self.baudrate = baudrate
        self.options = options
        self.writes = []
        self.closed = False
        self.__class__.instances.append(self)

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def read(self, length):
        return bytes((1,)) * length

    def flush(self):
        pass

    def reset_input_buffer(self):
        pass

    def close(self):
        self.closed = True


class ReplayPanel:
    """Interpret bitmap commands and payloads into a simulated RGB565 panel."""

    def __init__(self, initial):
        self.width, self.height = initial.size
        self.framebuffer = bytearray(display._rgb565le(initial))
        self.region = None
        self.payload = bytearray()
        self.regions = 0

    def write(self, data):
        data = bytes(data)
        if self.region is None:
            if len(data) != 6 or data[5] != 197:
                raise AssertionError("expected bitmap command")
            x0 = (data[0] << 2) | (data[1] >> 6)
            y0 = ((data[1] & 63) << 4) | (data[2] >> 4)
            x1 = ((data[2] & 15) << 6) | (data[3] >> 2)
            y1 = ((data[3] & 3) << 8) | data[4]
            self.region = (x0, y0, x1 + 1, y1 + 1)
            self.payload.clear()
        else:
            self.payload.extend(data)
            x0, y0, x1, y1 = self.region
            expected = 2 * (x1 - x0) * (y1 - y0)
            if len(self.payload) > expected:
                raise AssertionError("bitmap payload overflow")
            if len(self.payload) == expected:
                stride = 2 * (x1 - x0)
                for row in range(y1 - y0):
                    target = 2 * ((y0 + row) * self.width + x0)
                    self.framebuffer[target:target + stride] = self.payload[row * stride:(row + 1) * stride]
                self.region = None
                self.regions += 1
        return len(data)

    def reset_parser(self):
        self.region = None
        self.payload.clear()


class ProtocolTests(unittest.TestCase):
    def test_command_and_orientation_bytes(self):
        self.assertEqual(display._command(197, 0, 0, 319, 479), bytes.fromhex("00 00 04 fd df c5"))
        self.assertEqual(
            display._orientation_packet(90),
            bytes.fromhex("00 00 00 00 00 79 66 01 e0 01 40 00 00 00 00 00"),
        )
        self.assertEqual(display._brightness_packet(0), display._command(110, 255))
        self.assertEqual(display._brightness_packet(100), display._command(110, 0))

    def test_rgb565_little_endian_and_partial_rectangle(self):
        colors = Image.new("RGB", (3, 1))
        colors.putdata([(255, 0, 0), (0, 255, 0), (0, 0, 255)])
        self.assertEqual(display._rgb565le(colors), bytes.fromhex("00 f8 e0 07 1f 00"))

        old = Image.new("RGB", (8, 10), "black")
        new = old.copy()
        new.putpixel((5, 7), (255, 0, 0))
        link = FakeSerial("COM3", 115200)
        display._send_bitmap(link, new, old)
        self.assertEqual(link.writes, [display._command(197, 5, 7, 5, 7), bytes.fromhex("00 f8")])

    def test_regions_reconstruct_moved_values_without_old_pixels(self):
        old = Image.new("RGB", (480, 320), "black")
        draw = ImageDraw.Draw(old)
        draw.rectangle((25, 45, 60, 70), fill="red")
        draw.rectangle((390, 235, 425, 260), fill="green")
        new = Image.new("RGB", (480, 320), "black")
        draw = ImageDraw.Draw(new)
        draw.rectangle((50, 45, 85, 70), fill="blue")
        draw.rectangle((370, 235, 405, 260), fill="white")
        regions = display._dirty_regions(new, old)
        self.assertGreaterEqual(len(regions), 2)
        self.assertLess(sum(display._area(box) for box in regions), 480 * 320 // 4)
        panel = ReplayPanel(old)
        byte_count, count = display._send_bitmap(panel, new, old)
        self.assertEqual(count, panel.regions)
        self.assertEqual(byte_count, sum(6 + 2 * display._area(box) for box in regions))
        self.assertIsNone(panel.region)
        self.assertEqual(panel.framebuffer, display._rgb565le(new))

    def test_partial_failure_then_full_recovery_reconstructs_frame(self):
        old = Image.new("RGB", (480, 320), "black")
        new = old.copy()
        draw = ImageDraw.Draw(new)
        draw.rectangle((30, 40, 80, 70), fill="red")
        draw.rectangle((380, 240, 430, 270), fill="blue")

        class InterruptedPanel(ReplayPanel):
            failed = False

            def write(self, data):
                # Complete the first rectangle, then stop mid-payload of the
                # second. The next connection has to invalidate the old frame.
                if self.regions == 1 and self.region is not None and not self.failed:
                    half = max(1, len(data) // 2)
                    super().write(data[:half])
                    self.failed = True
                    raise display.serial.SerialException("unplugged mid-bitmap")
                return super().write(data)

        panel = InterruptedPanel(old)
        with self.assertRaises(display.serial.SerialException):
            display._send_bitmap(panel, new, old)
        self.assertEqual(panel.regions, 1)
        self.assertIsNotNone(panel.region)
        panel.reset_parser()  # The worker's reconnect/reset boundary.
        wire_bytes, count = display._send_bitmap(panel, new, None)
        self.assertEqual(count, 1)
        self.assertEqual(wire_bytes, 6 + 480 * 320 * 2)
        self.assertEqual(panel.framebuffer, display._rgb565le(new))

    def test_no_change_large_change_and_region_cap(self):
        base = Image.new("RGB", (480, 320), "black")
        self.assertEqual(display._dirty_regions(base, base.copy()), [])
        self.assertEqual(display._dirty_regions(Image.new("RGB", base.size, "white"), base), [(0, 0, 480, 320)])
        dots = base.copy()
        for y in range(8, 320, 35):
            for x in range(8, 480, 38):
                dots.putpixel((x, y), (255, 255, 255))
        regions = display._dirty_regions(dots, base)
        self.assertLessEqual(len(regions), display._MAX_REGIONS)
        for i, a in enumerate(regions):
            for b in regions[i + 1:]:
                self.assertTrue(a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])
        panel = ReplayPanel(base)
        display._send_bitmap(panel, dots, base)
        self.assertEqual(panel.framebuffer, display._rgb565le(dots))

    def test_disconnected_l_shapes_with_overlapping_bboxes_are_merged(self):
        base = Image.new("RGB", (480, 320), "black")
        changed = base.copy()
        draw = ImageDraw.Draw(changed)
        # The two active shapes are disjoint, but their component bounding
        # rectangles overlap at (200..220, 100..120). Their bounding union
        # is more expensive than two separate regions, so cost alone would
        # leave overlapping bitmap commands.
        draw.rectangle((20, 20, 220, 29), fill="white")
        draw.rectangle((20, 20, 29, 120), fill="white")
        draw.rectangle((200, 190, 400, 200), fill="white")
        draw.rectangle((390, 100, 400, 200), fill="white")
        regions = display._dirty_regions(changed, base)
        self.assertEqual(len(regions), 1)
        panel = ReplayPanel(base)
        display._send_bitmap(panel, changed, base)
        self.assertEqual(panel.framebuffer, display._rgb565le(changed))

    def test_port_selection_rejects_unrelated_serial_ports(self):
        ports = [
            SimpleNamespace(device="COM2", vid=0x1234, pid=0x5678, serial_number="OTHER"),
            SimpleNamespace(device="COM3", vid=0x1A86, pid=0x5722, serial_number="USB35INCHIPSV2"),
        ]
        with patch.object(display.list_ports, "comports", return_value=ports):
            self.assertEqual(display._select_port("AUTO"), "COM3")
            self.assertEqual(display._select_port("com3"), "COM3")
            with self.assertRaises(display.DeviceUnavailable):
                display._select_port("COM2")
            with self.assertRaisesRegex(display.DeviceUnavailable, "не найден"):
                display._select_port("COM2")


class WorkerTests(unittest.TestCase):
    def test_start_during_shutdown_restarts_after_serial_close(self):
        port = SimpleNamespace(device="COM3", vid=0x1A86, pid=0x5722, serial_number="USB35INCHIPSV2")
        close_started = threading.Event()
        allow_close = threading.Event()

        class BlockingClose(FakeSerial):
            instances = []

            def __init__(self, *args, **kwargs):
                self.assert_previous_closed()
                super().__init__(*args, **kwargs)

            @classmethod
            def assert_previous_closed(cls):
                if cls.instances:
                    assert cls.instances[-1].closed, "successor opened before old port closed"

            def close(self):
                if self is self.__class__.instances[0]:
                    close_started.set()
                    allow_close.wait(timeout=3)
                super().close()

        worker = display.DisplayWorker(reset_on_connect=False, port="COM3")
        with patch.object(display.list_ports, "comports", return_value=[port]), patch.object(
            display.serial, "Serial", BlockingClose
        ):
            try:
                worker.start()
                deadline = time.monotonic() + 3
                while not worker.status["connected"] and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(worker.status["connected"], worker.status)

                worker.stop()
                self.assertTrue(close_started.wait(timeout=3))
                worker.start()
                self.assertEqual(len(BlockingClose.instances), 1)
                allow_close.set()

                deadline = time.monotonic() + 3
                while (len(BlockingClose.instances) < 2 or not worker.status["connected"]) and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(len(BlockingClose.instances), 2)
                self.assertTrue(worker.status["connected"], worker.status)
            finally:
                allow_close.set()
                worker.stop()
                if worker._thread is not None:
                    worker._thread.join(timeout=2)

        self.assertTrue(all(link.closed for link in BlockingClose.instances))
        self.assertEqual(worker.status["message"], "Остановлено")

    def test_latest_frame_replaces_older_pending_frames(self):
        port = SimpleNamespace(device="COM3", vid=0x1A86, pid=0x5722, serial_number="USB35INCHIPSV2")
        FakeSerial.instances.clear()
        worker = display.DisplayWorker(reset_on_connect=False, port="AUTO", rotation=90, brightness=70)
        worker.submit(Image.new("RGB", (480, 320), "red"))
        worker.submit(Image.new("RGB", (480, 320), "green"))
        worker.submit(Image.new("RGB", (480, 320), "blue"))
        with patch.object(display.list_ports, "comports", return_value=[port]), patch.object(
            display.serial, "Serial", FakeSerial
        ):
            worker.start()
            deadline = time.monotonic() + 4
            while worker.status["frames"] < 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(worker.status["frames"], 1, worker.status)
            self.assertTrue(worker.status["connected"])
            worker.stop()
            worker._thread.join(timeout=2)

        link = FakeSerial.instances[-1]
        self.assertTrue(link.closed)
        self.assertEqual(link.port, "COM3")
        self.assertEqual(link.baudrate, 115200)
        self.assertEqual(link.options["write_timeout"], 1)
        self.assertEqual(link.writes[0], bytes((69,)) * 6)
        self.assertEqual(link.writes[1], bytes.fromhex("00 00 00 00 00 6d"))
        self.assertEqual(link.writes[2], display._orientation_packet(90))
        self.assertEqual(link.writes[3], display._brightness_packet(70))
        self.assertEqual(link.writes[4], display._command(197, 0, 0, 479, 319))
        self.assertEqual(link.writes[5][:2], bytes.fromhex("1f 00"))

    def test_failed_transfer_reconnects_and_resends_frame(self):
        port = SimpleNamespace(device="COM3", vid=0x1A86, pid=0x5722, serial_number="USB35INCHIPSV2")
        FakeSerial.instances.clear()

        class DropFirstBitmap(FakeSerial):
            failed = False

            def write(self, data):
                if data[:6] == display._command(197, 0, 0, 319, 479) and not self.__class__.failed:
                    self.__class__.failed = True
                    raise display.serial.SerialException("disconnected")
                return super().write(data)

        worker = display.DisplayWorker(reset_on_connect=False, port="COM3", rotation=0)
        worker.submit(Image.new("RGB", (320, 480), "white"))
        with patch.object(display.list_ports, "comports", return_value=[port]), patch.object(
            display.serial, "Serial", DropFirstBitmap
        ):
            worker.start()
            deadline = time.monotonic() + 4
            while worker.status["frames"] < 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(worker.status["frames"], 1, worker.status)
            worker.stop()
            worker._thread.join(timeout=2)

        self.assertEqual(len(FakeSerial.instances), 2)
        self.assertTrue(all(link.closed for link in FakeSerial.instances))
        self.assertEqual(FakeSerial.instances[-1].writes[1], bytes.fromhex("00 00 00 00 00 6d"))
        self.assertEqual(FakeSerial.instances[-1].writes[4], display._command(197, 0, 0, 319, 479))

    def test_recovery_resets_then_wakes_and_sends_full_frame(self):
        port=SimpleNamespace(device='COM3',vid=0x1A86,pid=0x5722,serial_number='USB35INCHIPSV2')
        FakeSerial.instances.clear()
        worker=display.DisplayWorker(rotation=0)
        worker.submit(Image.new('RGB',(320,480),'white'))
        with patch.object(display,'_RESET_DELAY',0), patch.object(display.list_ports,'comports',return_value=[port]), patch.object(display.serial,'Serial',FakeSerial):
            worker.start()
            deadline=time.monotonic()+3
            while worker.status['frames']<1 and time.monotonic()<deadline:time.sleep(.01)
            worker.stop()
            worker.wait_closed()
        self.assertEqual(len(FakeSerial.instances),2)
        self.assertEqual(FakeSerial.instances[0].writes,[display._command(101)])
        self.assertTrue(FakeSerial.instances[0].closed)
        self.assertIn(display._command(109),FakeSerial.instances[1].writes)
        self.assertIn(display._command(197,0,0,319,479),FakeSerial.instances[1].writes)
        self.assertEqual(worker.status['frames'],1)
        self.assertTrue(FakeSerial.instances[1].closed)

    def test_stop_drains_inflight_bitmap_before_closing(self):
        port=SimpleNamespace(device='COM3',vid=0x1A86,pid=0x5722,serial_number='USB35INCHIPSV2')
        entered=threading.Event();release=threading.Event()
        class SlowSerial(FakeSerial):
            instances=[]
            def write(self,data):
                if len(data)>16 and not entered.is_set():
                    entered.set();release.wait(3)
                return super().write(data)
        worker=display.DisplayWorker(rotation=0,reset_on_connect=False)
        worker.submit(Image.new('RGB',(320,480),'white'))
        with patch.object(display.list_ports,'comports',return_value=[port]),patch.object(display.serial,'Serial',SlowSerial):
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                worker.stop()
                self.assertFalse(SlowSerial.instances[0].closed)
            finally:
                release.set();worker.wait_closed()
        link=SlowSerial.instances[0]
        self.assertTrue(link.closed)
        self.assertEqual(sum(len(data) for data in link.writes if len(data)>16),320*480*2)
        self.assertEqual(worker.status['frames'],1)

    def test_brightness_clamps_and_dimensions_follow_rotation(self):
        worker = display.DisplayWorker(reset_on_connect=False, rotation=0, brightness=200)
        self.assertEqual(worker._brightness, 100)
        worker.submit(Image.new("RGB", (320, 480)))
        worker.configure("AUTO", 270, -10)
        self.assertEqual(worker._brightness, 0)
        with self.assertRaises(ValueError):
            worker.submit(Image.new("RGB", (320, 480)))
        worker.submit(Image.new("RGB", (480, 320)))


if __name__ == "__main__":
    unittest.main()
