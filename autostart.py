"""Register Aurora for automatic start at Windows logon.

The preferred backend is a per-user Task Scheduler task with the highest run
level.  It starts silently (no UAC prompt) and lets the read-only sensor helper
inherit administrator rights.  When the task cannot be created without an
elevation prompt, the per-user ``Run`` key is used as a normal-privilege
fallback.  The settings toggle always stays authoritative: the task launches
the executable with ``--autostart``, and the application exits immediately when
autostart was disabled in settings.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "AuroraMonitor"
TASK_NAME = "AuroraMonitor"
_TASK_TIMEOUT = 30
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def is_supported() -> bool:
    return os.name == "nt"


def is_elevated() -> bool:
    if not is_supported():
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _pythonw() -> Path:
    root = Path(__file__).resolve().parent
    candidate = root / ".venv" / "Scripts" / "pythonw.exe"
    if candidate.is_file():
        return candidate
    fallback = Path(sys.executable).with_name("pythonw.exe")
    return fallback if fallback.is_file() else Path(sys.executable)


def launch_argv() -> tuple[str, str, str]:
    """Return executable, arguments and working directory for autostart."""
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        return str(executable), "--autostart", str(executable.parent)
    root = Path(__file__).resolve().parent
    return (str(_pythonw()),
            f'"{root / "main.py"}" --autostart', str(root))


def run_command() -> str:
    """Command line written to the HKCU Run key."""
    executable, arguments, _ = launch_argv()
    return f'"{executable}" {arguments}'


def _winreg():
    import winreg

    return winreg


def read_run_value(key_path: str = RUN_KEY) -> str | None:
    if not is_supported():
        return None
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, kind = winreg.QueryValueEx(key, RUN_VALUE)
    except OSError:
        return None
    return str(value) if kind == winreg.REG_SZ else None


def set_run_enabled(enabled: bool, command: str | None = None,
                    key_path: str = RUN_KEY) -> None:
    """Create or remove the per-user startup value; raises OSError on failure."""
    if not is_supported():
        return
    winreg = _winreg()
    if not enabled:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                                winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, RUN_VALUE)
        except FileNotFoundError:
            pass
        except OSError as exc:
            if getattr(exc, "winerror", None) != 2:
                raise
        return
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ,
                          command or run_command())


def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _powershell(script: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_TASK_TIMEOUT, creationflags=_CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    message = (result.stderr or result.stdout or "").strip()
    return result.returncode == 0, message[:600]


def task_info() -> tuple[str, str] | None:
    """Return the task's executable and arguments, or None when it is absent."""
    script = (
        f"$t = Get-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} "
        "-ErrorAction SilentlyContinue; "
        "if ($t -and $t.Actions.Count -gt 0) { "
        "'AURORA_ACTION=' + $t.Actions[0].Execute + '|' + $t.Actions[0].Arguments }"
    )
    ok, output = _powershell(script)
    if not ok:
        return None
    for line in output.splitlines():
        if line.startswith("AURORA_ACTION="):
            execute, _, arguments = line[len("AURORA_ACTION="):].partition("|")
            return execute.strip(), arguments.strip()
    return None


def task_targets_current_build() -> bool:
    info = task_info()
    if info is None:
        return False
    execute, arguments = info
    expected_execute, expected_arguments, _ = launch_argv()
    return (execute.casefold() == expected_execute.casefold() and
            arguments.casefold() == expected_arguments.casefold())


def install_task() -> tuple[bool, str]:
    """Create/update the elevated logon task. Requires administrator rights."""
    executable, arguments, working_directory = launch_argv()
    user = (f"{os.environ.get('USERDOMAIN', '')}\\"
            f"{os.environ.get('USERNAME', '')}").lstrip("\\")
    if not user:
        return False, "Не удалось определить пользователя Windows"
    script = f"""
$ErrorActionPreference = 'Stop'
$action = New-ScheduledTaskAction -Execute {_ps_quote(executable)} `
    -Argument {_ps_quote(arguments)} -WorkingDirectory {_ps_quote(working_directory)}
$trigger = New-ScheduledTaskTrigger -AtLogOn -User {_ps_quote(user)}
$trigger.Delay = 'PT20S'
$principal = New-ScheduledTaskPrincipal -UserId {_ps_quote(user)} `
    -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} -Action $action `
    -Trigger $trigger -Principal $principal -Settings $settings `
    -Description {_ps_quote('АВРОРА — монитор компьютера')} -Force | Out-Null
"""
    return _powershell(script)


def remove_task() -> tuple[bool, str]:
    script = (
        f"$t = Get-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} "
        "-ErrorAction SilentlyContinue; "
        f"if ($t) {{ Unregister-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} "
        "-Confirm:$false }"
    )
    return _powershell(script)


def sync(enabled: bool, *, allow_task: bool = True) -> tuple[bool, str | None, str]:
    """Apply the requested setting and report (ok, error, active-backend)."""
    if not is_supported():
        return True, None, "unsupported"

    errors: list[str] = []
    if not enabled:
        try:
            set_run_enabled(False)
        except OSError as exc:
            errors.append(f"реестр: {exc}")
        if allow_task and is_elevated() and task_info() is not None:
            ok, message = remove_task()
            if not ok:
                errors.append(f"задача: {message}")
        return (not errors), ("; ".join(errors) or None), "disabled"

    if allow_task and is_elevated():
        if task_targets_current_build():
            try:
                set_run_enabled(False)
            except OSError:
                pass
            return True, None, "task"
        ok, message = install_task()
        if ok:
            try:
                set_run_enabled(False)
            except OSError:
                pass
            return True, None, "task"
        errors.append(f"задача: {message}")
    elif allow_task and task_info() is not None:
        if task_targets_current_build():
            return True, None, "task"
        errors.append("задача автозапуска указывает на другой файл; нужны права администратора")

    try:
        set_run_enabled(True)
        return True, None, "run"
    except OSError as exc:
        errors.append(f"реестр: {exc}")
    return False, "; ".join(errors), "none"
