"""Optional, read-only FPS source from RTSS shared memory.

Layout source: RTSS SDK ``Include/RTSSSharedMemory.h`` as vendored at
https://github.com/jaxFF/RTSSTweaks/blob/master/thirdparty/rtss-sdk/Include/RTSSSharedMemory.h

Only an already-existing ``RTSSSharedMemoryV2`` mapping is opened. This module
does not launch RTSS, install hooks, create mappings, or write shared memory.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import struct
from typing import Any


_MAPPING_NAME = "RTSSSharedMemoryV2"
_FILE_MAP_READ = 0x0004
_SIGNATURE = 0x52545353  # MSVC 'RTSS', stored as b'SSTR' on little-endian Windows.
_HEADER_SIZE = 72  # Through v2.16 foreground app fields.
_ENTRY_PREFIX_SIZE = 284  # Through dwFrameTime.
_MAX_SPAN = 64 * 1024 * 1024
_NON_GAME_PROCESSES = frozenset({
    "explorer.exe", "dwm.exe", "searchhost.exe", "shellexperiencehost.exe",
    "applicationframehost.exe", "taskmgr.exe", "cmd.exe", "powershell.exe",
    "pwsh.exe", "windowsterminal.exe", "conhost.exe", "python.exe",
    "pythonw.exe", "notepad.exe", "code.exe", "cursor.exe", "devenv.exe",
    "chrome.exe", "msedge.exe", "msedgewebview2.exe", "firefox.exe",
    "brave.exe", "opera.exe", "vivaldi.exe", "discord.exe", "spotify.exe",
    "vlc.exe", "slack.exe", "teams.exe", "chatgpt.exe",
    "steam.exe", "epicgameslauncher.exe", "rtss.exe", "msiafterburner.exe",
    "auroramonitor.exe", "usbmonitor.exe",
})


class _Unavailable(RuntimeError):
    pass


class _MemoryBasicInformation(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


def _header_location(header: bytes, mapped_size: int) -> tuple[int, int, int, int]:
    """Validate SDK version and bounded foreground entry address."""
    if len(header) < _HEADER_SIZE:
        raise _Unavailable("Заголовок RTSS повреждён")
    fields = struct.unpack_from("<18I", header)
    signature, version = fields[:2]
    if signature != _SIGNATURE:
        raise _Unavailable("Подпись памяти RTSS недействительна")
    if version >> 16 != 2 or version & 0xFFFF < 16:
        raise _Unavailable("Версия памяти RTSS не поддерживается")

    entry_size, array_offset, array_count = fields[2:5]
    osd_entry_size, osd_offset, osd_count = fields[5:8]
    foreground_index, foreground_pid = fields[16:18]
    if not (_ENTRY_PREFIX_SIZE <= entry_size <= 65536 and 1 <= array_count <= 256):
        raise _Unavailable("Размер записей RTSS недействителен")
    if not (512 <= osd_entry_size <= 1024 * 1024 and 1 <= osd_count <= 8):
        raise _Unavailable("Размер OSD-записей RTSS недействителен")
    osd_end = osd_offset + osd_entry_size * osd_count
    array_end = array_offset + entry_size * array_count
    if not (
        _HEADER_SIZE <= osd_offset < osd_end <= array_offset < array_end <= mapped_size
        and array_end <= _MAX_SPAN
        and array_offset % 4 == 0
        and osd_offset % 4 == 0
    ):
        raise _Unavailable("Смещения памяти RTSS недействительны")
    if foreground_pid == 0 or foreground_index >= array_count:
        raise _Unavailable("Активная игра в RTSS не найдена")
    if fields[9] & 1:
        raise _Unavailable("RTSS обновляет общую память")
    return array_offset + foreground_index * entry_size, foreground_pid, version, entry_size


def _parse_entry(entry: bytes, foreground_pid: int, tick_ms: int) -> dict[str, Any]:
    if len(entry) < _ENTRY_PREFIX_SIZE:
        raise _Unavailable("Запись игры RTSS повреждена")
    process_id = struct.unpack_from("<I", entry, 0)[0]
    if process_id == 0 or process_id != foreground_pid:
        raise _Unavailable("Активная игра в RTSS не найдена")
    name_bytes = entry[4:264].split(b"\0", 1)[0]
    process_name = name_bytes.decode("mbcs" if os.name == "nt" else "utf-8", errors="replace")
    time0, time1, frames, frame_time = struct.unpack_from("<4I", entry, 268)
    age = ((tick_ms & 0xFFFFFFFF) - time1) & 0xFFFFFFFF
    if time0 == 0 or time1 == 0 or age > 5000:
        raise _Unavailable("Нет свежих данных FPS от RTSS")

    # The SDK specifies dwFrameTime in microseconds. Prefer the current frame
    # rate from that same measurement, with its once-per-second formula as a
    # fallback when frame time is unavailable.
    frametime_ms = round(frame_time / 1000, 2) if 0 < frame_time <= 10_000_000 else None
    fps = round(1_000_000 / frame_time, 1) if frametime_ms is not None else None
    if fps is None:
        interval = (time1 - time0) & 0xFFFFFFFF
        if 0 < interval <= 10_000 and frames > 0:
            fps = round(1000 * frames / interval, 1)
    if fps is None or fps <= 0 or fps > 10_000:
        raise _Unavailable("Нет корректных данных FPS от RTSS")
    return {
        "fps": fps,
        "frametime_ms": frametime_ms,
        "process_name": process_name or None,
        "status": "Данные FPS получены из RTSS",
    }


def _read_mapping() -> tuple[bytes, int, int]:
    """Copy the bounded foreground entry while a read-only view is mapped."""
    if os.name != "nt":
        raise _Unavailable("RTSS доступен только в Windows")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenFileMappingW.restype = wintypes.HANDLE
    kernel.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
    kernel.MapViewOfFile.restype = ctypes.c_void_p
    kernel.VirtualQuery.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MemoryBasicInformation), ctypes.c_size_t]
    kernel.VirtualQuery.restype = ctypes.c_size_t
    kernel.GetTickCount64.restype = ctypes.c_uint64
    kernel.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    kernel.UnmapViewOfFile.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL

    handle = kernel.OpenFileMappingW(_FILE_MAP_READ, False, _MAPPING_NAME)
    if not handle:
        raise _Unavailable("RTSS не запущен или общая память недоступна")
    view = None
    try:
        view = kernel.MapViewOfFile(handle, _FILE_MAP_READ, 0, 0, 0)
        if not view:
            raise _Unavailable("Не удалось прочитать общую память RTSS")
        region = _MemoryBasicInformation()
        if kernel.VirtualQuery(view, ctypes.byref(region), ctypes.sizeof(region)) != ctypes.sizeof(region):
            raise _Unavailable("Не удалось проверить размер памяти RTSS")
        offset_in_region = int(view) - int(region.BaseAddress or 0)
        mapped_size = int(region.RegionSize) - offset_in_region
        if not (0 <= offset_in_region < region.RegionSize and mapped_size >= _HEADER_SIZE):
            raise _Unavailable("Размер памяти RTSS недействителен")
        header = ctypes.string_at(view, _HEADER_SIZE)
        entry_offset, foreground_pid, _, _ = _header_location(header, mapped_size)
        entry = ctypes.string_at(int(view) + entry_offset, _ENTRY_PREFIX_SIZE)
        return entry, foreground_pid, int(kernel.GetTickCount64())
    finally:
        if view:
            kernel.UnmapViewOfFile(view)
        kernel.CloseHandle(handle)


def _foreground_pid() -> int | None:
    """The active OS window must still belong to RTSS's foreground entry."""
    if os.name != "nt":
        return None
    kernel = ctypes.WinDLL("user32", use_last_error=True)
    kernel.GetForegroundWindow.argtypes = []
    kernel.GetForegroundWindow.restype = wintypes.HWND
    kernel.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                 ctypes.POINTER(wintypes.DWORD)]
    kernel.GetWindowThreadProcessId.restype = wintypes.DWORD
    window = kernel.GetForegroundWindow()
    if not window:
        return None
    pid = wintypes.DWORD()
    if not kernel.GetWindowThreadProcessId(window, ctypes.byref(pid)):
        return None
    return int(pid.value) or None


def _is_candidate_game(process_name: str | None) -> bool:
    """Reject common GPU accelerated desktop apps which RTSS can also hook."""
    if not process_name:
        return False
    name = process_name.replace("/", "\\").rsplit("\\", 1)[-1].casefold()
    return name.endswith(".exe") and name not in _NON_GAME_PROCESSES


def read_gaming() -> dict[str, Any]:
    """Return FPS only for a fresh RTSS entry matching a candidate game window."""
    try:
        entry, foreground_pid, tick_ms = _read_mapping()
        sample = _parse_entry(entry, foreground_pid, tick_ms)
        if _foreground_pid() != foreground_pid:
            raise _Unavailable("Активная игра в RTSS не найдена")
        if not _is_candidate_game(sample["process_name"]):
            raise _Unavailable("Активная игра в RTSS не найдена")
        sample["game_detected"] = True
        return sample
    except _Unavailable as exc:
        message = str(exc)
    except Exception:
        message = "Не удалось прочитать данные FPS из RTSS"
    return {"fps": None, "frametime_ms": None, "process_name": None, "status": message}
