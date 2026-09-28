"""Focused tests for telemetry parsing and non-blocking snapshot calculations."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import telemetry


class ParsingTests(unittest.TestCase):
    def test_nvidia_csv_preserves_real_values_and_missing_sensors(self) -> None:
        sample = "NVIDIA GeForce RTX 5060, 37, 51, 1200, 8192, 76.4, N/A\n"
        self.assertEqual(telemetry._parse_nvidia_smi(sample), {
            "gpu_name": "NVIDIA GeForce RTX 5060",
            "gpu_percent": 37.0,
            "gpu_temp": 51.0,
            "gpu_memory_used_mb": 1200.0,
            "gpu_memory_total_mb": 8192.0,
            "gpu_power_w": 76.4,
            "gpu_clock_mhz": None,
        })
        self.assertIsNone(telemetry._parse_nvidia_smi("malformed output"))

    def test_lhm_chooses_cpu_sensor_and_ignores_gpu_temperature(self) -> None:
        sensors = [
            {"Name": "GPU Core", "Identifier": "/gpu-nvidia/0/temperature/0",
             "SensorType": "Temperature", "Value": 66},
            {"Name": "CPU Core #1", "Identifier": "/amdcpu/0/temperature/1",
             "SensorType": "Temperature", "Value": 54},
            {"Name": "CPU Package", "Identifier": "/amdcpu/0/temperature/0",
             "SensorType": "Temperature", "Value": 58},
            {"Name": "GPU Fan", "Identifier": "/gpu-nvidia/0/fan/0",
             "SensorType": "Fan", "Value": 1800},
            {"Name": "CPU Fan", "Identifier": "/lpc/0/fan/0",
             "SensorType": "Fan", "Value": 1020},
        ]
        self.assertEqual(telemetry._parse_lhm_sensors(json.dumps(sensors)), (58.0, 1020.0))
        self.assertEqual(telemetry._parse_lhm_sensors("not json"), (None, None))

    def test_privileged_lhm_selects_real_fans_and_cpu_clocks(self) -> None:
        sensors = [
            {"Name": "CPU", "Identifier": "/lpc/0/temperature/0", "SensorType": "Temperature", "Value": 43},
            {"Name": "Core (Tctl/Tdie)", "Identifier": "/amdcpu/0/temperature/2", "SensorType": "Temperature", "Value": 80.25},
            {"Name": "Cores (Average)", "Identifier": "/amdcpu/0/clock/1", "SensorType": "Clock", "Value": 4940},
            {"Name": "Cores (Average Effective)", "Identifier": "/amdcpu/0/clock/2", "SensorType": "Clock", "Value": 1505},
            {"Name": "CPU Fan", "Identifier": "/lpc/0/fan/0", "SensorType": "Fan", "Value": 2625},
            {"Name": "Pump Fan", "Identifier": "/lpc/0/fan/1", "SensorType": "Fan", "Value": 5405},
            {"Name": "System Fan #1", "Identifier": "/lpc/0/fan/2", "SensorType": "Fan", "Value": 0},
            {"Name": "System Fan #2", "Identifier": "/lpc/0/fan/3", "SensorType": "Fan", "Value": 3636},
            {"Name": "GPU Fan", "Identifier": "/gpu-nvidia/0/fan/1", "SensorType": "Fan", "Value": 1599},
            {"Name": "GPU Fan Control", "Identifier": "/gpu-nvidia/0/control/0", "SensorType": "Control", "Value": 42},
        ]
        parsed = telemetry._select_lhm_sensors(sensors)
        self.assertEqual(parsed["cpu_temp"], 80.25)
        self.assertEqual(parsed["cpu_mhz"], 4940)
        self.assertEqual(parsed["cpu_effective_mhz"], 1505)
        self.assertEqual(parsed["cpu_fan_rpm"], 2625)
        self.assertEqual(parsed["pump_fan_rpm"], 5405)
        self.assertEqual(parsed["case_fan_rpm"], 3636)
        self.assertEqual(parsed["gpu_fan_rpm"], 1599)
        self.assertEqual(len(parsed["fan_candidates"]), 5)
        overridden = telemetry._select_lhm_sensors(sensors, cpu_fan_id="/lpc/0/fan/3")
        self.assertEqual(overridden["cpu_fan_rpm"], 3636)
        self.assertIsNone(telemetry._select_lhm_sensors(
            sensors, cpu_fan_id="/missing")["cpu_fan_rpm"])

    def test_zero_cpu_temperature_is_not_reported(self) -> None:
        parsed = telemetry._select_lhm_sensors([
            {"Name": "Core (Tctl/Tdie)", "Identifier": "/amdcpu/0/temperature/2",
             "SensorType": "Temperature", "Value": 0},
            {"Name": "CPU Socket", "Identifier": "/lpc/0/temperature/4",
             "SensorType": "Temperature", "Value": 72},
        ])
        self.assertIsNone(parsed["cpu_temp"])

    def test_smbios_memory_uses_configured_data_rate(self) -> None:
        modules = [{"Capacity": 16 * 1024 ** 3, "ConfiguredClockSpeed": 6000,
                    "Speed": 4800, "SMBIOSMemoryType": 34, "PartNumber": "KF560C40-16"}] * 2
        parsed = telemetry._parse_memory_modules(json.dumps(modules))
        self.assertEqual(parsed["ram_speed_mt"], 6000)
        self.assertEqual(parsed["ram_clock_mhz"], 3000)
        self.assertEqual(parsed["ram_type"], "DDR5")
        self.assertEqual(parsed["ram_installed_gb"], 32)
        self.assertEqual(parsed["ram_module_count"], 2)
        self.assertEqual(parsed["ram_model"], "KF560C40-16")

    def test_hardware_file_must_be_recent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hardware.json"
            payload = {"timestamp": 100.0, "sensors": [
                {"Name": "CPU Fan", "Identifier": "/lpc/0/fan/0",
                 "SensorType": "Fan", "Value": 2625}]}
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(telemetry._read_hardware_file(path, 104.0, None, None)[
                "cpu_fan_rpm"], 2625)
            self.assertIsNone(telemetry._read_hardware_file(path, 108.0, None, None))

    def test_sensor_host_error_is_visible_without_fake_readings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hardware.json"
            path.write_text(json.dumps({"timestamp": 100.0, "sensors": [],
                                        "error": "PawnIO unavailable"}), encoding="utf-8")
            with patch.object(telemetry, "psutil", SimpleNamespace(
                    boot_time=lambda: 1.0, cpu_percent=lambda interval=None: 0.0)):
                collector = telemetry.Telemetry(background=False, nvidia_smi="",
                                                hardware_path=path,
                                                wall_time=lambda: 101.0)
                sample = collector.sample()
        self.assertIsNone(sample["cpu_temp"])
        self.assertEqual(sample["sensor_status"], "error")
        self.assertIn("PawnIO unavailable", sample["sensor_hint"])


class SnapshotTests(unittest.TestCase):
    def test_cpu_uses_pdh_utility_and_falls_back_to_psutil_busy_time(self) -> None:
        readings = iter((14.25, None))
        counter = SimpleNamespace(read=lambda: next(readings))
        fake_psutil = SimpleNamespace(boot_time=lambda: 1.0,
                                      cpu_percent=lambda interval=None: 3.5)
        with (patch.object(telemetry, "psutil", fake_psutil),
              patch.object(telemetry, "_open_cpu_counter", return_value=counter)):
            collector = telemetry.Telemetry(background=False, nvidia_smi="")
            first = collector.sample()
            second = collector.sample()
        self.assertEqual(first["cpu_percent"], 14.25)
        self.assertEqual(first["cpu_source"], "Windows PDH Processor Utility")
        self.assertEqual(second["cpu_percent"], 3.5)
        self.assertEqual(second["cpu_source"], "psutil busy time")

    def test_fresh_privileged_snapshot_overrides_wmi_cache(self) -> None:
        fake_psutil = SimpleNamespace(boot_time=lambda: 1.0,
                                      cpu_percent=lambda interval=None: 12.0,
                                      cpu_freq=lambda: SimpleNamespace(current=3800.0))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hardware.json"
            path.write_text(json.dumps({"timestamp": 100.0,
                                        "memory_modules": [{"Capacity": 16 * 1024 ** 3,
                                                            "ConfiguredClockSpeed": 6000,
                                                            "Speed": 4800,
                                                            "SMBIOSMemoryType": 34,
                                                            "PartNumber": "KF560C40-16"}] * 2,
                                        "sensors": [
                {"Name": "Core (Tctl/Tdie)", "Identifier": "/amdcpu/0/temperature/2",
                 "SensorType": "Temperature", "Value": 80.25},
                {"Name": "CPU Fan", "Identifier": "/lpc/0/fan/0",
                 "SensorType": "Fan", "Value": 2625},
                {"Name": "GPU Fan", "Identifier": "/gpu-nvidia/0/fan/1",
                 "SensorType": "Fan", "Value": 1599},
                {"Name": "Cores (Average)", "Identifier": "/amdcpu/0/clock/1",
                 "SensorType": "Clock", "Value": 4940},
            ]}), encoding="utf-8")
            with patch.object(telemetry, "psutil", fake_psutil):
                collector = telemetry.Telemetry(background=False, nvidia_smi="",
                                                hardware_path=path,
                                                monotonic=lambda: 10.0,
                                                wall_time=lambda: 101.0)
                collector._cpu_temp = 40.0
                snapshot = collector.sample()
        self.assertEqual(snapshot["cpu_temp"], 80.25)
        self.assertEqual(snapshot["cpu_fan_rpm"], 2625)
        self.assertEqual(snapshot["fan_rpm"], 2625)
        self.assertEqual(snapshot["gpu_fan_rpm"], 1599)
        self.assertEqual(snapshot["cpu_mhz"], 4940)
        self.assertEqual(snapshot["sensor_status"], "ok")
        self.assertEqual(snapshot["cpu_temp_source"], "LibreHardwareMonitor")
        self.assertEqual(snapshot["helper_age_seconds"], 1.0)
        self.assertEqual(snapshot["ram_speed_mt"], 6000)
        self.assertEqual(snapshot["ram_type"], "DDR5")
        self.assertEqual(snapshot["ram_module_count"], 2)

    def test_rates_and_units_are_based_on_counter_deltas(self) -> None:
        clock = iter((100.0, 102.0))
        counters = iter((SimpleNamespace(bytes_sent=1000, bytes_recv=2000),
                         SimpleNamespace(bytes_sent=1600, bytes_recv=3000)))
        fake_psutil = SimpleNamespace(
            boot_time=lambda: 50.0,
            cpu_percent=lambda interval=None: 24.5,
            cpu_freq=lambda: SimpleNamespace(current=4250.0),
            virtual_memory=lambda: SimpleNamespace(total=16 * 1024 ** 3,
                                                    available=6 * 1024 ** 3,
                                                    percent=62.5),
            disk_usage=lambda path: SimpleNamespace(total=100 * 1024 ** 3,
                                                     used=40 * 1024 ** 3,
                                                     percent=40.0),
            net_io_counters=lambda pernic=False: next(counters),
        )
        with patch.object(telemetry, "psutil", fake_psutil), patch.object(
                telemetry, "_cpu_name", return_value="AMD Ryzen 7 7700"):
            collector = telemetry.Telemetry(background=False, monotonic=lambda: next(clock),
                                            wall_time=lambda: 150.0, nvidia_smi="")
            first = collector.sample()
            second = collector.sample()
        self.assertIsNone(first["net_down_bps"])
        self.assertEqual(second["net_down_bps"], 500.0)
        self.assertEqual(second["net_up_bps"], 300.0)
        self.assertEqual(second["ram_used_gb"], 10.0)
        self.assertEqual(second["disk_total_gb"], 100.0)
        self.assertEqual(second["uptime_seconds"], 100.0)
        self.assertEqual(second["cpu_name"], "AMD Ryzen 7 7700")
        self.assertIsNone(second["cpu_temp"])
        self.assertIsNone(second["fan_rpm"])
        self.assertIsNone(second["gpu_temp"])

    def test_probe_failure_keeps_unknown_sensors_none(self) -> None:
        def failed_run(*args, **kwargs):
            raise OSError("device absent")

        fake_psutil = SimpleNamespace(boot_time=lambda: 1.0,
                                      cpu_percent=lambda interval=None: 0.0)
        with patch.object(telemetry, "psutil", fake_psutil):
            collector = telemetry.Telemetry(background=False, runner=failed_run,
                                            nvidia_smi="nvidia-smi")
            collector._probe_gpu()
            collector._probe_sensors()
        self.assertEqual(collector._gpu_status, "unavailable")
        self.assertEqual(collector._sensor_status, "unavailable")
        self.assertIsNone(collector._cpu_temp)
        self.assertIsNone(collector._gpu["gpu_temp"])

    def test_failed_gpu_refresh_clears_stale_live_values(self) -> None:
        calls = iter((SimpleNamespace(returncode=0,
                                      stdout="NVIDIA GeForce RTX 5060, 40, 55, 1000, 8000, 80, 1500"),
                      SimpleNamespace(returncode=1, stdout="")))
        fake_psutil = SimpleNamespace(boot_time=lambda: 1.0,
                                      cpu_percent=lambda interval=None: 0.0)
        with patch.object(telemetry, "psutil", fake_psutil):
            collector = telemetry.Telemetry(background=False, runner=lambda *a, **k: next(calls),
                                            nvidia_smi="nvidia-smi")
            collector._probe_gpu()
            self.assertEqual(collector._gpu["gpu_temp"], 55.0)
            collector._probe_gpu()
        self.assertEqual(collector._gpu["gpu_name"], "NVIDIA GeForce RTX 5060")
        self.assertIsNone(collector._gpu["gpu_temp"])
        self.assertEqual(collector._gpu_status, "unavailable")


if __name__ == "__main__":
    unittest.main()
