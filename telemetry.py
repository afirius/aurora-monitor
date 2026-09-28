"""Real Windows hardware telemetry for the Aurora monitor.

``sample`` never waits for an external process. NVIDIA and LibreHardwareMonitor
are queried on daemon threads and their most recent readings are cached.
Unavailable measurements stay ``None`` rather than being estimated.
"""

from __future__ import annotations

import csv
import ctypes
import io
import json
import math
import os
import platform
from pathlib import Path
import shutil
import subprocess
import threading
import time
from typing import Any

try:
    import psutil
except ImportError:  # A clear error is raised when Telemetry is instantiated.
    psutil = None


GPU_FIELDS = (
    "gpu_name",
    "gpu_percent",
    "gpu_temp",
    "gpu_memory_used_mb",
    "gpu_memory_total_mb",
    "gpu_power_w",
    "gpu_clock_mhz",
)


class _WindowsProcessorUtility:
    """Read Task Manager's CPU basis through the English PDH counter.

    ``psutil.cpu_percent`` measures non-idle time. Windows Processor Utility
    also accounts for processor performance scaling, so the two legitimately
    differ. This query is primed once and subsequently read without blocking.
    """

    class _Value(ctypes.Structure):
        _fields_ = [("status", ctypes.c_uint32), ("value", ctypes.c_double)]

    def __init__(self) -> None:
        self._pdh = ctypes.WinDLL("pdh", use_last_error=True)
        handle = ctypes.c_void_p
        self._pdh.PdhOpenQueryW.argtypes = [ctypes.c_wchar_p, ctypes.c_size_t,
                                            ctypes.POINTER(handle)]
        self._pdh.PdhOpenQueryW.restype = ctypes.c_uint32
        self._pdh.PdhAddEnglishCounterW.argtypes = [handle, ctypes.c_wchar_p,
                                                    ctypes.c_size_t, ctypes.POINTER(handle)]
        self._pdh.PdhAddEnglishCounterW.restype = ctypes.c_uint32
        self._pdh.PdhCollectQueryData.argtypes = [handle]
        self._pdh.PdhCollectQueryData.restype = ctypes.c_uint32
        self._pdh.PdhGetFormattedCounterValue.argtypes = [
            handle, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(self._Value)]
        self._pdh.PdhGetFormattedCounterValue.restype = ctypes.c_uint32
        self._pdh.PdhCloseQuery.argtypes = [handle]
        self._pdh.PdhCloseQuery.restype = ctypes.c_uint32
        self._query = handle()
        self._counter = handle()
        try:
            if self._pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query)) != 0:
                raise OSError("PDH query unavailable")
            if self._pdh.PdhAddEnglishCounterW(
                    self._query,
                    r"\Processor Information(_Total)\% Processor Utility",
                    0, ctypes.byref(self._counter)) != 0:
                raise OSError("PDH Processor Utility counter unavailable")
            if self._pdh.PdhCollectQueryData(self._query) != 0:
                raise OSError("PDH CPU counter could not be primed")
        except Exception:
            self.close()
            raise

    def read(self) -> float | None:
        if not self._query or self._pdh.PdhCollectQueryData(self._query) != 0:
            return None
        value = self._Value()
        if self._pdh.PdhGetFormattedCounterValue(
                self._counter, 0x00000200, None, ctypes.byref(value)) != 0:
            return None
        if value.status not in (0, 1) or not math.isfinite(value.value):
            return None
        # The default PDH percentage format caps at 100 for display. Reject
        # malformed results instead of substituting a guessed percentage.
        return value.value if 0 <= value.value <= 100 else None

    def close(self) -> None:
        if getattr(self, "_query", None):
            self._pdh.PdhCloseQuery(self._query)
            self._query = ctypes.c_void_p()

    def __del__(self) -> None:
        self.close()


def _open_cpu_counter() -> _WindowsProcessorUtility | None:
    if os.name != "nt":
        return None
    try:
        return _WindowsProcessorUtility()
    except Exception:
        # PDH is optional; a broken/missing counter must not stop telemetry.
        return None


def _number(value: str, kind: type = float) -> float | int | None:
    value = value.strip()
    if not value or value.lower() in {"n/a", "not supported", "[not supported]"}:
        return None
    try:
        number = kind(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _parse_nvidia_smi(output: str) -> dict[str, Any] | None:
    """Parse the first NVIDIA GPU row from --format=csv,noheader,nounits."""
    try:
        row = next(csv.reader(io.StringIO(output)))
    except (StopIteration, csv.Error):
        return None
    if len(row) != 7:
        return None
    name = row[0].strip() or None
    return dict(
        gpu_name=name,
        gpu_percent=_number(row[1]),
        gpu_temp=_number(row[2]),
        gpu_memory_used_mb=_number(row[3]),
        gpu_memory_total_mb=_number(row[4]),
        gpu_power_w=_number(row[5]),
        gpu_clock_mhz=_number(row[6]),
    )


def _select_lhm_sensors(sensors: list[dict[str, Any]],
                        cpu_fan_id: str | None = None,
                        gpu_fan_id: str | None = None) -> dict[str, Any]:
    """Select only real temperature/Fan (RPM) sensors from LHM data."""
    readings: dict[str, Any] = dict(cpu_temp=None, cpu_fan_rpm=None,
                                    gpu_fan_rpm=None, pump_fan_rpm=None,
                                    case_fan_rpm=None, cpu_mhz=None,
                                    cpu_effective_mhz=None, fan_candidates=[])
    temperatures: list[tuple[int, float]] = []
    cpu_fans: list[tuple[int, float]] = []
    gpu_fans: list[tuple[int, float]] = []
    pump_fans: list[float] = []
    case_fans: list[float] = []
    for sensor in sensors:
        if not isinstance(sensor, dict):
            continue
        name = str(sensor.get("Name") or "").strip()
        identifier = str(sensor.get("Identifier") or "").strip()
        parent = str(sensor.get("Parent") or sensor.get("HardwareName") or "").strip()
        sensor_type = str(sensor.get("SensorType") or "").lower()
        value = _number(str(sensor.get("Value", "")))
        if value is None:
            continue
        lowered = name.lower()
        lowered_id = identifier.lower()
        if sensor_type == "clock" and any(marker in lowered_id for marker in
                                          ("/amdcpu/", "/intelcpu/")):
            if 0 < value <= 10000:
                if lowered == "cores (average)":
                    readings["cpu_mhz"] = value
                elif lowered == "cores (average effective)":
                    readings["cpu_effective_mhz"] = value
        if sensor_type == "temperature":
            is_cpu = any(marker in lowered_id for marker in ("/amdcpu/", "/intelcpu/", "/cpu/"))
            if not is_cpu:
                continue
            # Ryzen 7000 may expose a placeholder 0 when low-level access
            # fails; never present it as a real CPU temperature.
            if not 0 < value <= 130:
                continue
            priority = 0 if ("package" in lowered or "tctl/tdie" in lowered or "tctl" in lowered) else 1
            temperatures.append((priority, value))
        elif sensor_type == "fan" and 0 <= value <= 20_000:
            # LibreHardwareMonitor's SensorType.Fan is explicitly measured RPM.
            # Control sensors are percentages and must never be converted.
            readings["fan_candidates"].append(dict(
                identifier=identifier, name=name, parent=parent, rpm=value))
            if "pump" in lowered:
                pump_fans.append(value)
            elif "system fan" in lowered or "case fan" in lowered or "chassis fan" in lowered:
                if value > 0:
                    case_fans.append(value)
            if cpu_fan_id:
                if lowered_id == cpu_fan_id.lower():
                    cpu_fans.append((-1, value))
            elif "cpu" in lowered or "processor" in lowered:
                cpu_fans.append((0 if "fan" in lowered else 1, value))
            if gpu_fan_id:
                if lowered_id == gpu_fan_id.lower():
                    gpu_fans.append((-1, value))
            elif any(marker in lowered_id for marker in
                     ("/gpu-nvidia/", "/gpu-amd/", "/gpu-intel/")):
                gpu_fans.append((0, value))
            elif "gpu fan" in lowered:
                gpu_fans.append((1, value))
    temperatures.sort(key=lambda item: item[0])
    cpu_fans.sort(key=lambda item: item[0])
    gpu_fans.sort(key=lambda item: item[0])
    readings["cpu_temp"] = temperatures[0][1] if temperatures else None
    readings["cpu_fan_rpm"] = cpu_fans[0][1] if cpu_fans else None
    readings["gpu_fan_rpm"] = gpu_fans[0][1] if gpu_fans else None
    readings["pump_fan_rpm"] = pump_fans[0] if pump_fans else None
    readings["case_fan_rpm"] = case_fans[0] if case_fans else None
    return readings


def _parse_lhm_readings(output: str, cpu_fan_id: str | None = None,
                        gpu_fan_id: str | None = None) -> dict[str, Any]:
    """Parse WMI's JSON representation of LHM sensor objects."""
    try:
        sensors = json.loads(output)
    except (TypeError, ValueError):
        sensors = []
    if isinstance(sensors, dict):
        sensors = [sensors]
    if not isinstance(sensors, list):
        sensors = []
    return _select_lhm_sensors(sensors, cpu_fan_id, gpu_fan_id)


def _parse_lhm_sensors(output: str) -> tuple[float | None, float | None]:
    """Compatibility wrapper for earlier two-field callers."""
    readings = _parse_lhm_readings(output)
    return readings["cpu_temp"], readings["cpu_fan_rpm"]


def _cpu_name() -> str | None:
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                name = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
                if name:
                    return " ".join(name.split())
        except (OSError, ImportError):
            pass
    return platform.processor().strip() or None


def _nvidia_smi_path() -> str | None:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    for root in (os.environ.get("ProgramFiles"), os.environ.get("WINDIR")):
        if not root:
            continue
        candidates = (
            os.path.join(root, "NVIDIA Corporation", "NVSMI", "nvidia-smi.exe"),
            os.path.join(root, "System32", "nvidia-smi.exe"),
        )
        for candidate in candidates:
            if os.path.isfile(candidate):
                return candidate
    return None


def _lhm_running() -> bool | None:
    """Distinguish a missing companion from one with inaccessible sensors."""
    try:
        for process in psutil.process_iter(["name"]):
            name = str(process.info.get("name") or "").lower()
            if name in {"librehardwaremonitor.exe", "librehardwaremonitor.net.10.exe"}:
                return True
        return False
    except Exception:
        return None


def _read_hardware_file(path: Path, now: float, cpu_fan_id: str | None,
                        gpu_fan_id: str | None) -> dict[str, Any] | None:
    """Accept only an atomic, recent snapshot from the privileged sensor host."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("sensors"), list):
        return None
    timestamp = _number(str(payload.get("timestamp", "")))
    if timestamp is None or not -2 <= now - timestamp < 8:
        return None
    readings = _select_lhm_sensors(payload["sensors"], cpu_fan_id, gpu_fan_id)
    readings["helper_age_seconds"] = max(0.0, now - timestamp)
    readings["helper_error"] = str(payload.get("error") or "")[:180] or None
    if isinstance(payload.get("memory_modules"), list):
        readings["memory_modules"] = _parse_memory_modules(json.dumps(payload["memory_modules"]))
    return readings


def _parse_memory_modules(output: str) -> dict[str, Any]:
    """Read installed DIMMs from SMBIOS; configured speed is in MT/s."""
    empty = dict(ram_speed_mt=None, ram_clock_mhz=None, ram_type=None,
                 ram_installed_gb=None, ram_module_count=None, ram_model=None)
    try:
        modules = json.loads(output)
    except (TypeError, ValueError):
        return empty
    if isinstance(modules, dict):
        modules = [modules]
    if not isinstance(modules, list):
        return empty
    installed = [module for module in modules if isinstance(module, dict)
                 and (_number(str(module.get("Capacity", "")), int) or 0) > 0]
    if not installed:
        return empty
    capacities = [int(_number(str(module["Capacity"]), int) or 0)
                  for module in installed]
    speeds = []
    for module in installed:
        configured = _number(str(module.get("ConfiguredClockSpeed", "")), int)
        advertised = _number(str(module.get("Speed", "")), int)
        selected = configured if configured and configured > 0 else advertised
        if selected and selected > 0:
            speeds.append(int(selected))
    speed_mt = min(speeds) if speeds else None
    types = {int(t) for module in installed if
             (t := _number(str(module.get("SMBIOSMemoryType", "")), int)) is not None}
    memory_type = {26: "DDR4", 34: "DDR5", 24: "DDR3"}.get(next(iter(types))) if len(types) == 1 else None
    models = [str(module.get("PartNumber") or "").strip() for module in installed]
    models = [model for model in models if model]
    return dict(ram_speed_mt=speed_mt,
                ram_clock_mhz=speed_mt / 2 if speed_mt is not None else None,
                ram_type=memory_type,
                ram_installed_gb=sum(capacities) / (1024 ** 3),
                ram_module_count=len(installed),
                ram_model=models[0] if models and len(set(models)) == 1 else None)


class Telemetry:
    """Collect CPU, GPU, RAM, disk and network values for a UI refresh loop.

    All rates are bytes/second. Temperatures are Celsius; NVIDIA GPU memory is
    reported in MiB and RAM/disk in GiB. Unavailable values are ``None``. NVIDIA metrics refresh
    at most every two seconds; LHM WMI sensors refresh at most every 15 seconds.
    An atomic, fresh sensor-host snapshot takes priority over WMI.
    """

    def __init__(self, *, gpu_interval: float = 2.0, sensor_interval: float = 15.0,
                 runner: Any = None, monotonic: Any = None, wall_time: Any = None,
                 background: bool = True, nvidia_smi: str | None = None,
                 hardware_path: Path | str | None = None,
                 cpu_fan_sensor_id: str | None = None,
                 gpu_fan_sensor_id: str | None = None) -> None:
        if psutil is None:
            raise RuntimeError("psutil is required: install project requirements")
        self._runner = runner or subprocess.run
        self._monotonic = monotonic or time.monotonic
        self._wall_time = wall_time or time.time
        self._background = background
        self._gpu_interval = max(0.25, gpu_interval)
        self._sensor_interval = max(1.0, sensor_interval)
        self._nvidia_smi = nvidia_smi if nvidia_smi is not None else _nvidia_smi_path()
        local_appdata = os.environ.get("LOCALAPPDATA") or str(Path.home())
        self._hardware_path = (Path(hardware_path) if hardware_path is not None else
                               Path(local_appdata) / "AuroraMonitor" / "hardware.json")
        self._cpu_fan_id = cpu_fan_sensor_id or os.environ.get("AURORA_CPU_FAN_SENSOR_ID")
        self._gpu_fan_id = gpu_fan_sensor_id or os.environ.get("AURORA_GPU_FAN_SENSOR_ID")
        self._cpu_name = _cpu_name()
        self._disk_path = os.environ.get("SystemDrive", "C:") + "\\" if os.name == "nt" else os.path.abspath(os.sep)
        self._boot_time = self._safe(lambda: psutil.boot_time())
        self._cpu_counter = _open_cpu_counter()
        self._net_previous: tuple[float, int, int] | None = None
        self._gpu: dict[str, Any] = dict.fromkeys(GPU_FIELDS)
        self._cpu_temp: float | None = None
        self._fan_rpm: float | None = None
        self._gpu_fan_rpm: float | None = None
        self._pump_fan_rpm: float | None = None
        self._case_fan_rpm: float | None = None
        self._sensor_cpu_mhz: float | None = None
        self._cpu_effective_mhz: float | None = None
        self._fan_candidates: list[dict[str, Any]] = []
        self._memory_modules: dict[str, Any] = _parse_memory_modules("")
        self._memory_probe_started = False
        self._gpu_status = "unavailable" if not self._nvidia_smi else "pending"
        self._sensor_status = "pending" if os.name == "nt" else "unavailable"
        self._sensor_hint: str | None = None
        self._lock = threading.Lock()
        self._gpu_busy = False
        self._sensor_busy = False
        self._next_gpu = 0.0
        self._next_sensor = 0.0
        try:
            psutil.cpu_percent(interval=None)  # Prime psutil's non-blocking delta.
        except Exception:
            pass

    @staticmethod
    def _safe(callback: Any) -> Any:
        try:
            return callback()
        except Exception:
            return None

    def _run(self, command: list[str], timeout: float) -> Any:
        kwargs: dict[str, Any] = dict(capture_output=True, text=True, timeout=timeout,
                                      encoding="utf-8", errors="replace")
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return self._runner(command, **kwargs)

    def _probe_gpu(self) -> None:
        parsed = None
        try:
            result = self._run([
                self._nvidia_smi or "nvidia-smi",
                "--query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total,power.draw,clocks.current.graphics",
                "--format=csv,noheader,nounits",
            ], timeout=2.0)
            if result.returncode == 0:
                parsed = _parse_nvidia_smi(result.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass
        with self._lock:
            if parsed is not None:
                self._gpu.update(parsed)
                self._gpu_status = "ok"
            else:
                # A failed poll must never present an old load or temperature
                # as a current reading. The model name is stable hardware data.
                for field in GPU_FIELDS:
                    if field != "gpu_name":
                        self._gpu[field] = None
                self._gpu_status = "unavailable"
            self._gpu_busy = False

    def _probe_sensors(self) -> None:
        readings = _select_lhm_sensors([])
        try:
            script = (
                "$ErrorActionPreference='Stop'; "
                "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
                "Get-CimInstance -Namespace 'root/LibreHardwareMonitor' -ClassName Sensor "
                "-ErrorAction Stop | Select-Object Name,SensorType,Value,Identifier,Parent "
                "| ConvertTo-Json -Compress -Depth 3"
            )
            result = self._run(["powershell.exe", "-NoProfile", "-NonInteractive",
                                "-Command", script], timeout=2.5)
            if result.returncode == 0:
                readings = _parse_lhm_readings(result.stdout, self._cpu_fan_id,
                                               self._gpu_fan_id)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass
        helper_running = _lhm_running() if (readings["cpu_temp"] is None and
                                           readings["cpu_fan_rpm"] is None) else None
        with self._lock:
            self._cpu_temp = readings["cpu_temp"]
            self._fan_rpm = readings["cpu_fan_rpm"]
            self._gpu_fan_rpm = readings["gpu_fan_rpm"]
            self._pump_fan_rpm = readings["pump_fan_rpm"]
            self._case_fan_rpm = readings["case_fan_rpm"]
            self._sensor_cpu_mhz = readings["cpu_mhz"]
            self._cpu_effective_mhz = readings["cpu_effective_mhz"]
            self._fan_candidates = readings["fan_candidates"]
            if readings["cpu_temp"] is not None or readings["cpu_fan_rpm"] is not None:
                self._sensor_status = "ok"
                self._sensor_hint = None
            elif helper_running is False:
                self._sensor_status = "helper_not_running"
                self._sensor_hint = "Для датчиков CPU запустите помощник Aurora Monitor с правами администратора."
            else:
                self._sensor_status = "unavailable"
                self._sensor_hint = "Датчики CPU недоступны: проверьте помощник Aurora Monitor, LibreHardwareMonitor и PawnIO."
            self._sensor_busy = False

    def _probe_memory(self) -> None:
        try:
            script = (
                "$ErrorActionPreference='Stop'; "
                "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
                "Get-CimInstance Win32_PhysicalMemory -ErrorAction Stop | "
                "Select-Object Capacity,ConfiguredClockSpeed,Speed,SMBIOSMemoryType,PartNumber "
                "| ConvertTo-Json -Compress -Depth 3"
            )
            result = self._run(["powershell.exe", "-NoProfile", "-NonInteractive",
                                "-Command", script], timeout=5.0)
            modules = _parse_memory_modules(result.stdout) if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired, ValueError):
            modules = None
        if modules:
            with self._lock:
                self._memory_modules = modules

    def _schedule_probes(self, now: float, helper_fresh: bool = False) -> None:
        if not self._background:
            return
        with self._lock:
            gpu_due = bool(self._nvidia_smi) and not self._gpu_busy and now >= self._next_gpu
            if gpu_due:
                self._gpu_busy = True
                self._next_gpu = now + self._gpu_interval
            sensors_due = (os.name == "nt" and not helper_fresh and
                           not self._sensor_busy and now >= self._next_sensor)
            if sensors_due:
                self._sensor_busy = True
                self._next_sensor = now + self._sensor_interval
            memory_due = os.name == "nt" and not self._memory_probe_started
            if memory_due:
                self._memory_probe_started = True
        if gpu_due:
            threading.Thread(target=self._probe_gpu, name="aurora-gpu", daemon=True).start()
        if sensors_due:
            threading.Thread(target=self._probe_sensors, name="aurora-sensors", daemon=True).start()
        if memory_due:
            threading.Thread(target=self._probe_memory, name="aurora-memory", daemon=True).start()

    def sample(self) -> dict[str, Any]:
        """Return a flat snapshot; safe to call frequently from the UI thread."""
        now = self._monotonic()
        wall_now = self._wall_time()
        helper = _read_hardware_file(self._hardware_path, wall_now,
                                     self._cpu_fan_id, self._gpu_fan_id)
        self._schedule_probes(now, helper_fresh=helper is not None)
        busy_percent = self._safe(lambda: psutil.cpu_percent(interval=None))
        utility_percent = self._safe(self._cpu_counter.read) if self._cpu_counter else None
        cpu_percent = utility_percent if utility_percent is not None else busy_percent
        cpu_source = ("Windows PDH Processor Utility" if utility_percent is not None
                      else "psutil busy time" if busy_percent is not None else None)
        freq = self._safe(lambda: psutil.cpu_freq())
        memory = self._safe(lambda: psutil.virtual_memory())
        disk = self._safe(lambda: psutil.disk_usage(self._disk_path))
        network = self._safe(lambda: psutil.net_io_counters(pernic=False))

        net_down = net_up = None
        if network is not None:
            sent, received = network.bytes_sent, network.bytes_recv
            if self._net_previous is not None:
                prior_time, prior_sent, prior_received = self._net_previous
                elapsed = now - prior_time
                if elapsed > 0 and sent >= prior_sent and received >= prior_received:
                    net_down = (received - prior_received) / elapsed
                    net_up = (sent - prior_sent) / elapsed
            self._net_previous = (now, sent, received)

        gib = 1024 ** 3
        snapshot: dict[str, Any] = dict(
            cpu_name=self._cpu_name,
            cpu_percent=cpu_percent,
            cpu_source=cpu_source,
            cpu_temp=None,
            cpu_mhz=freq.current if freq is not None else None,
            gpu_name=None,
            gpu_percent=None,
            gpu_temp=None,
            gpu_memory_used_mb=None,
            gpu_memory_total_mb=None,
            gpu_power_w=None,
            gpu_clock_mhz=None,
            ram_used_gb=(memory.total - memory.available) / gib if memory else None,
            ram_total_gb=memory.total / gib if memory else None,
            ram_percent=memory.percent if memory else None,
            disk_used_gb=disk.used / gib if disk else None,
            disk_total_gb=disk.total / gib if disk else None,
            disk_percent=disk.percent if disk else None,
            net_down_bps=net_down,
            net_up_bps=net_up,
            uptime_seconds=max(0.0, wall_now - self._boot_time) if self._boot_time else None,
            fan_rpm=None,
            cpu_fan_rpm=None,
            gpu_fan_rpm=None,
            pump_fan_rpm=None,
            case_fan_rpm=None,
            cpu_effective_mhz=None,
            fan_candidates=[],
            helper_age_seconds=None,
            helper_error=None,
            disk_path=self._disk_path,
            gpu_status=None,
            sensor_status=None,
            sensor_hint=None,
            cpu_temp_source=None,
            fan_source=None,
            cpu_fan_source=None,
            gpu_fan_source=None,
            gpu_source=None,
        )
        with self._lock:
            snapshot.update(self._gpu)
            snapshot["cpu_temp"] = self._cpu_temp
            snapshot["fan_rpm"] = self._fan_rpm
            snapshot["cpu_fan_rpm"] = self._fan_rpm
            snapshot["gpu_fan_rpm"] = self._gpu_fan_rpm
            snapshot["pump_fan_rpm"] = self._pump_fan_rpm
            snapshot["case_fan_rpm"] = self._case_fan_rpm
            snapshot["cpu_effective_mhz"] = self._cpu_effective_mhz
            snapshot["fan_candidates"] = list(self._fan_candidates)
            if self._sensor_cpu_mhz is not None:
                snapshot["cpu_mhz"] = self._sensor_cpu_mhz
            snapshot.update(self._memory_modules)
            snapshot["gpu_status"] = self._gpu_status
            snapshot["sensor_status"] = self._sensor_status
            snapshot["sensor_hint"] = self._sensor_hint
        sensor_source = "LibreHardwareMonitor WMI"
        if helper is not None:
            memory_from_helper = helper.get("memory_modules")
            if memory_from_helper and memory_from_helper["ram_module_count"] is not None:
                snapshot.update(memory_from_helper)
            for field in ("cpu_temp", "cpu_fan_rpm", "gpu_fan_rpm",
                          "pump_fan_rpm", "case_fan_rpm", "cpu_effective_mhz",
                          "fan_candidates", "helper_age_seconds", "helper_error"):
                snapshot[field] = helper[field]
            if helper["cpu_mhz"] is not None:
                snapshot["cpu_mhz"] = helper["cpu_mhz"]
            snapshot["fan_rpm"] = helper["cpu_fan_rpm"]
            sensor_source = "LibreHardwareMonitor"
            snapshot["sensor_status"] = ("error" if helper["helper_error"] else
                                         "ok" if helper["cpu_temp"] is not None and
                                         helper["cpu_fan_rpm"] is not None else "partial")
            if helper["helper_error"]:
                snapshot["sensor_hint"] = "Помощник датчиков: " + helper["helper_error"]
            elif helper["cpu_temp"] is None:
                snapshot["sensor_hint"] = "Температура CPU недоступна: проверьте PawnIO и права помощника."
            elif helper["cpu_fan_rpm"] is None:
                snapshot["sensor_hint"] = "Обороты вентилятора CPU недоступны среди аппаратных датчиков LibreHardwareMonitor."
            else:
                snapshot["sensor_hint"] = None
        if snapshot["cpu_temp"] is not None:
            snapshot["cpu_temp_source"] = sensor_source
        if snapshot["fan_rpm"] is not None:
            snapshot["fan_source"] = sensor_source
            snapshot["cpu_fan_source"] = sensor_source
        if snapshot["gpu_fan_rpm"] is not None:
            snapshot["gpu_fan_source"] = sensor_source
        if snapshot["gpu_name"] is not None:
            snapshot["gpu_source"] = "nvidia-smi"
        return snapshot
