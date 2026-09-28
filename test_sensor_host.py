"""Regression tests for publishing hardware readings from the sensor host."""

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sensor_host


class SensorHostPublicationTests(unittest.TestCase):
    def test_save_retries_a_transient_replacement_denial(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hardware.json"
            original_replace = Path.replace
            attempts = []

            def replace(source, destination):
                attempts.append(destination)
                if len(attempts) == 1:
                    raise PermissionError("file is briefly in use")
                return original_replace(source, destination)

            sensors = [{"Name": "CPU Fan", "Value": 1450}]
            with patch.object(Path, "replace", replace), patch.object(
                    sensor_host.time, "sleep") as sleep:
                sensor_host.save(path, sensors, [], parent_pid=10)

            self.assertEqual(len(attempts), 2)
            sleep.assert_called_once_with(.05)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["sensors"], sensors)

    def test_exhausted_retries_leave_the_last_good_snapshot_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hardware.json"
            previous = '{"sensors": [{"Name": "CPU Fan", "Value": 1400}]}'
            path.write_text(previous, encoding="utf-8")

            with patch.object(Path, "replace", side_effect=PermissionError("busy")) as replace, \
                    patch.object(sensor_host.time, "sleep") as sleep:
                with self.assertRaises(PermissionError):
                    sensor_host.save(path, [{"Name": "CPU Fan", "Value": 1500}], [])

            self.assertEqual(replace.call_count, 6)
            self.assertEqual(sleep.call_count, 5)
            self.assertEqual(path.read_text(encoding="utf-8"), previous)

    def test_main_keeps_polling_after_publication_failure(self):
        sensor = SimpleNamespace(Name="CPU Fan", Identifier="/lpc/0/fan/0",
                                 SensorType="Fan", Value=1450)
        hardware = SimpleNamespace(Name="Motherboard", HardwareType="SuperIO",
                                   Sensors=[sensor], SubHardware=[], Update=lambda: None)

        class Computer:
            Hardware = [hardware]

            def Open(self):
                pass

            def Close(self):
                pass

        clr = types.ModuleType("clr")
        clr.AddReference = lambda path: None
        lhm = types.ModuleType("LibreHardwareMonitor")
        lhm.__path__ = []
        lhm_hardware = types.ModuleType("LibreHardwareMonitor.Hardware")
        lhm_hardware.Computer = Computer
        real_save = sensor_host.save
        published = []

        def save(path, rows, modules, parent_pid=0, error=None):
            published.append((rows, error))
            if len(published) == 1:
                raise PermissionError("destination in use")
            real_save(path, rows, modules, parent_pid, error)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hardware.json"
            with patch.dict(sys.modules, {"clr": clr, "LibreHardwareMonitor": lhm,
                                          "LibreHardwareMonitor.Hardware": lhm_hardware}), \
                    patch.object(sensor_host, "read_memory_modules", return_value=[]), \
                    patch.object(sensor_host, "save", side_effect=save), \
                    patch.object(sensor_host.time, "sleep"), \
                    patch.object(sys, "argv", ["sensor_host.py", "--output", str(path), "--once"]):
                sensor_host.main()

            self.assertEqual(len(published), 3)
            self.assertTrue(all(len(rows) == 1 and error is None
                                for rows, error in published))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))[
                "sensors"][0]["Value"], 1450)


if __name__ == "__main__":
    unittest.main()
