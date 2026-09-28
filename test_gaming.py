"""Synthetic RTSS layout tests; no RTSS process or mapping is created."""

import struct
import unittest
from unittest.mock import patch

import gaming


def _header(**changes):
    fields = [0] * 18
    fields[0] = gaming._SIGNATURE
    fields[1] = 0x00020010
    fields[2] = 284  # app entry size
    fields[3] = 584  # app array follows one 512-byte OSD entry
    fields[4] = 2
    fields[5] = 512
    fields[6] = 72
    fields[7] = 1
    fields[16] = 1
    fields[17] = 1234
    for index, value in changes.items():
        fields[int(index)] = value
    return struct.pack("<18I", *fields)


def _entry(*, pid=1234, tick=100_000, frame_time=16_667):
    entry = bytearray(284)
    struct.pack_into("<I", entry, 0, pid)
    entry[4:12] = b"game.exe"
    struct.pack_into("<4I", entry, 268, tick - 1100, tick - 100, 60, frame_time)
    return bytes(entry)


class GamingTests(unittest.TestCase):
    def test_valid_foreground_entry_and_microsecond_frametime(self):
        offset, pid, version, size = gaming._header_location(_header(), 1152)
        self.assertEqual((offset, pid, version, size), (868, 1234, 0x00020010, 284))
        sample = gaming._parse_entry(_entry(), pid, 100_000)
        self.assertEqual(sample["fps"], 60.0)
        self.assertEqual(sample["frametime_ms"], 16.67)
        self.assertEqual(sample["process_name"], "game.exe")

    def test_rejects_bad_signature_version_and_out_of_bounds_arrays(self):
        for header in (
            _header(**{"0": 0}),
            _header(**{"1": 0x0002000F}),
            _header(**{"2": 16}),
            _header(**{"3": 0xFFFFFF00}),
            _header(**{"4": 257}),
            _header(**{"16": 2}),
        ):
            with self.subTest(header=header[:20].hex()), self.assertRaises(gaming._Unavailable):
                gaming._header_location(header, 1152)

    def test_rejects_stale_or_wrong_process_and_falls_back_to_window_fps(self):
        with self.assertRaises(gaming._Unavailable):
            gaming._parse_entry(_entry(pid=4321), 1234, 100_000)
        with self.assertRaises(gaming._Unavailable):
            gaming._parse_entry(_entry(), 1234, 110_000)
        result = gaming._parse_entry(_entry(frame_time=0), 1234, 100_000)
        self.assertEqual(result["fps"], 60.0)
        self.assertIsNone(result["frametime_ms"])

    def test_unavailable_source_returns_russian_status(self):
        with patch.object(gaming, "_read_mapping", side_effect=gaming._Unavailable("RTSS не запущен")):
            result = gaming.read_gaming()
        self.assertEqual(result, {
            "fps": None,
            "frametime_ms": None,
            "process_name": None,
            "status": "RTSS не запущен",
        })

    def test_fps_requires_matching_foreground_game_window(self):
        with (patch.object(gaming, "_read_mapping", return_value=(_entry(), 1234, 100_000)),
              patch.object(gaming, "_foreground_pid", return_value=1234)):
            result = gaming.read_gaming()
        self.assertTrue(result["game_detected"])
        self.assertEqual(result["fps"], 60.0)

        with (patch.object(gaming, "_read_mapping", return_value=(_entry(), 1234, 100_000)),
              patch.object(gaming, "_foreground_pid", return_value=5678)):
            result = gaming.read_gaming()
        self.assertIsNone(result["fps"])
        self.assertNotIn("game_detected", result)

    def test_rtss_hooked_browser_is_not_treated_as_game(self):
        browser = bytearray(_entry())
        browser[4:264] = b"chrome.exe" + b"\0" * (260 - len("chrome.exe"))
        with (patch.object(gaming, "_read_mapping", return_value=(bytes(browser), 1234, 100_000)),
              patch.object(gaming, "_foreground_pid", return_value=1234)):
            result = gaming.read_gaming()
        self.assertIsNone(result["fps"])
        self.assertNotIn("game_detected", result)


if __name__ == "__main__":
    unittest.main()
