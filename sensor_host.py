"""Read-only elevated sensor process. No network and no fan-control writes.

The desktop stays unelevated. This helper owns the LHM instance and publishes
only sensor values, then exits when its exact parent process exits.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil


def read_memory_modules():
    """Query SMBIOS once while elevated; the desktop cannot access this WMI class."""
    command = (
        "$ErrorActionPreference='Stop'; "
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
        "Get-CimInstance Win32_PhysicalMemory -ErrorAction Stop | "
        "Select-Object Capacity,ConfiguredClockSpeed,Speed,SMBIOSMemoryType,PartNumber "
        "| ConvertTo-Json -Compress -Depth 3"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=6, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode != 0:
            return []
        modules = json.loads(result.stdout)
        return modules if isinstance(modules, list) else [modules] if isinstance(modules, dict) else []
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []


def save(path, sensors, memory_modules, parent_pid=0, error=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    temp.write_text(json.dumps({'timestamp': time.time(), 'pid': os.getpid(),
                                'parent_pid': parent_pid, 'sensors': sensors,
                                'memory_modules': memory_modules, 'error': error},
                               ensure_ascii=False), encoding='utf-8')
    # On Windows a simultaneous read can briefly prevent replacement of the
    # destination. Keep the complete snapshot in the temp file while retrying.
    for attempt in range(6):
        try:
            temp.replace(path)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(.05)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--parent-pid', type=int, default=0)
    parser.add_argument('--parent-created', type=float, default=0)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if not args.once and (args.parent_pid <= 0 or args.parent_created <= 0):
        parser.error('A live parent is required')
    def parent_alive():
        if args.once:
            return True
        try:
            parent = psutil.Process(args.parent_pid)
            return abs(parent.create_time() - args.parent_created) < .01 and parent.is_running()
        except psutil.Error:
            return False
    if not parent_alive():
        return
    mutex_handle = None
    if os.name == 'nt' and not args.once:
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle.restype = ctypes.c_bool
        identity = f'{args.parent_pid}_{int(args.parent_created * 1000)}'
        mutex_handle = kernel.CreateMutexW(None, True,
                                           f'Local\\AuroraMonitor_SensorHost_{identity}')
        if not mutex_handle or ctypes.get_last_error() == 183:
            if mutex_handle:
                kernel.CloseHandle(mutex_handle)
            return
    folder = Path(__file__).resolve().parent / 'vendor' / 'lhm'
    sys.path.insert(0, str(folder))
    pc = None
    try:
        memory_modules = read_memory_modules()
        import clr
        clr.AddReference(str(folder / 'LibreHardwareMonitorLib.dll'))
        from LibreHardwareMonitor.Hardware import Computer
        pc = Computer()
        pc.IsCpuEnabled = True
        pc.IsMotherboardEnabled = True
        pc.IsGpuEnabled = True
        pc.Open()
        def collect(hw, rows):
            hw.Update()
            for sensor in hw.Sensors:
                kind = str(sensor.SensorType)
                if kind in ('Temperature', 'Fan', 'Clock', 'Power'):
                    rows.append({'Name': str(sensor.Name), 'Identifier': str(sensor.Identifier),
                                 'SensorType': kind, 'Value': float(sensor.Value) if sensor.Value is not None else None,
                                 'HardwareName': str(hw.Name), 'HardwareType': str(hw.HardwareType)})
            for child in hw.SubHardware:
                collect(child, rows)
        iteration = 0
        while parent_alive():
            started = time.monotonic()
            rows = []
            for hw in pc.Hardware:
                collect(hw, rows)
            try:
                save(args.output, rows, memory_modules, args.parent_pid)
            except OSError:
                # A publication failure must not stop hardware polling or
                # replace a previously valid snapshot with empty sensors.
                pass
            iteration += 1
            if args.once and iteration >= 3:
                break
            time.sleep(max(.1, 1 - (time.monotonic() - started)))
    except Exception as exc:
        save(args.output, [], memory_modules if 'memory_modules' in locals() else [],
             args.parent_pid, str(exc))
    finally:
        if pc is not None:
            pc.Close()
        if mutex_handle is not None:
            kernel.CloseHandle(mutex_handle)


if __name__ == '__main__':
    main()
