import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import autostart
import resources


class AutostartTests(unittest.TestCase):
    def test_frozen_build_uses_its_own_executable(self):
        with patch.object(sys, "frozen", True, create=True), \
                patch.object(sys, "executable",
                             r"C:\Program Files\35inchENG\AuroraMonitor.exe"):
            executable, arguments, directory = autostart.launch_argv()
            command = autostart.run_command()
        self.assertTrue(executable.endswith("AuroraMonitor.exe"))
        self.assertEqual(arguments, "--autostart")
        self.assertEqual(Path(directory), Path(executable).parent)
        self.assertEqual(command, f'"{executable}" --autostart')

    def test_source_launch_uses_pythonw_and_main_script(self):
        with patch.object(sys, "frozen", False, create=True):
            executable, arguments, directory = autostart.launch_argv()
        self.assertTrue(executable.lower().endswith("pythonw.exe"))
        self.assertIn("main.py", arguments)
        self.assertTrue(arguments.endswith("--autostart"))
        self.assertTrue((Path(directory) / "main.py").is_file())

    @unittest.skipUnless(os.name == "nt", "Windows registry only")
    def test_run_key_roundtrip_uses_a_temporary_key(self):
        key_path = r"Software\AuroraMonitorTests\Autostart"
        command = r'"C:\Test\AuroraMonitor.exe" --autostart'
        try:
            autostart.set_run_enabled(True, command, key_path=key_path)
            self.assertEqual(autostart.read_run_value(key_path=key_path), command)
            autostart.set_run_enabled(False, key_path=key_path)
            self.assertIsNone(autostart.read_run_value(key_path=key_path))
        finally:
            self._remove_test_key(r"Software\AuroraMonitorTests")

    @staticmethod
    def _remove_test_key(path: str) -> None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0,
                                winreg.KEY_ALL_ACCESS) as parent:
                try:
                    winreg.DeleteKey(parent, "Autostart")
                except FileNotFoundError:
                    pass
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software", 0, winreg.KEY_ALL_ACCESS) as parent:
                winreg.DeleteKey(parent, "AuroraMonitorTests")
        except FileNotFoundError:
            pass

    def test_unelevated_sync_uses_run_key_fallback(self):
        with patch("autostart.is_supported", return_value=True), \
                patch("autostart.is_elevated", return_value=False), \
                patch("autostart.task_info", return_value=None), \
                patch("autostart.set_run_enabled") as set_run:
            outcome = autostart.sync(True)
        self.assertEqual(outcome, (True, None, "run"))
        set_run.assert_called_once_with(True)

    def test_elevated_sync_prefers_the_scheduled_task(self):
        with patch("autostart.is_supported", return_value=True), \
                patch("autostart.is_elevated", return_value=True), \
                patch("autostart.task_targets_current_build", return_value=False), \
                patch("autostart.install_task", return_value=(True, "")), \
                patch("autostart.set_run_enabled") as set_run:
            outcome = autostart.sync(True)
        self.assertEqual(outcome, (True, None, "task"))
        set_run.assert_called_once_with(False)

    def test_disabling_removes_the_run_key(self):
        with patch("autostart.is_supported", return_value=True), \
                patch("autostart.is_elevated", return_value=False), \
                patch("autostart.set_run_enabled") as set_run:
            outcome = autostart.sync(False)
        self.assertEqual(outcome, (True, None, "disabled"))
        set_run.assert_called_once_with(False)

    def test_failed_task_and_run_key_are_reported(self):
        with patch("autostart.is_supported", return_value=True), \
                patch("autostart.is_elevated", return_value=True), \
                patch("autostart.task_targets_current_build", return_value=False), \
                patch("autostart.install_task", return_value=(False, "access denied")), \
                patch("autostart.set_run_enabled", side_effect=OSError("read-only")):
            ok, error, backend = autostart.sync(True)
        self.assertFalse(ok)
        self.assertIn("access denied", error)
        self.assertIn("read-only", error)
        self.assertEqual(backend, "none")

    def test_powershell_quoting_escapes_apostrophes(self):
        self.assertEqual(autostart._ps_quote("O'Brien"), "'O''Brien'")

    def test_resource_path_uses_pyinstaller_bundle_root(self):
        with patch.object(sys, "frozen", True, create=True), \
                patch.object(sys, "_MEIPASS", r"C:\Temp\_MEI123", create=True):
            self.assertEqual(resources.resource_path("assets", "icon.png"),
                             Path(r"C:\Temp\_MEI123\assets\icon.png"))


if __name__ == "__main__":
    unittest.main()
