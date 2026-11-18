"""Version Windows : programmes lancés sans fenêtre de console (sauf l'invite de commandes de connexion)."""

import ctypes
import importlib.util
import pathlib
import subprocess
import unittest
from unittest import mock

LAUNCHER = pathlib.Path(__file__).resolve().parent.parent / "windows" / "launcher.py"


class Consoles(unittest.TestCase):
    def test_programs_start_without_a_console(self):
        spec = importlib.util.spec_from_file_location("launcher", LAUNCHER)
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
        calls = []
        with mock.patch.object(subprocess.Popen, "__init__", lambda self, *args, creationflags=0, **kwargs: calls.append(creationflags)):
            launcher.hide_consoles()
            subprocess.Popen(["claude", "auth", "status"])
            subprocess.Popen(["cmd.exe", "/k", "setup.cmd"], creationflags=0x10)  # CREATE_NEW_CONSOLE : reste visible
        self.assertEqual(calls, [0x08000000, 0x10])


class SingleInstance(unittest.TestCase):
    def launcher(self):
        spec = importlib.util.spec_from_file_location("launcher", LAUNCHER)
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
        return launcher

    def test_already_exists_is_read_through_the_thread_error_of_ctypes(self):
        launcher = self.launcher()
        fake = mock.Mock()
        fake.CreateMutexW.return_value = 1
        for code, expected in ((183, False), (0, True)):
            with mock.patch.object(ctypes, "WinDLL", create=True, return_value=fake) as windll, \
                    mock.patch.object(ctypes, "get_last_error", create=True, return_value=code):
                self.assertIs(launcher.single_instance(pathlib.Path("/tmp/x")), expected)
            windll.assert_called_once_with("kernel32", use_last_error=True)
        fake.GetLastError.assert_not_called()
