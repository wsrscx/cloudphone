from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cloudphone.db import Database
from cloudphone.devices import AdbResult
from cloudphone.ime import restore_abandoned_imes


class _Adb:
    def __init__(self, returncode: int = 0, current_ime: str = "different/.Ime"):
        self.returncode = returncode
        self.current_ime = current_ime
        self.calls: list[tuple[str, ...]] = []

    async def shell(self, serial: str, *args: str, **_kwargs: object) -> AdbResult:
        self.calls.append((serial, *args))
        if args == ("settings", "get", "secure", "default_input_method"):
            return AdbResult(self.current_ime.encode(), b"", 0)
        if args[:2] == ("ime", "set") and self.returncode == 0:
            self.current_ime = args[2]
        return AdbResult(b"", b"", self.returncode)


class ImeRestoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temporary.name) / "test.db")
        self.db.upsert_device("dev_test", "serial", "device", "model", "15")
        self.db.save_ime_restore("dev_test", "original/.Ime", "socket")

    async def asyncTearDown(self) -> None:
        self.temporary.cleanup()

    async def test_successful_restore_clears_record(self) -> None:
        adb = _Adb()
        await restore_abandoned_imes(self.db, adb)  # type: ignore[arg-type]
        self.assertEqual(self.db.active_ime_restores(), [])
        self.assertIn(("serial", "ime", "set", "original/.Ime"), adb.calls)

    async def test_failed_restore_is_retained_for_reconnect(self) -> None:
        adb = _Adb(returncode=1)
        await restore_abandoned_imes(self.db, adb)  # type: ignore[arg-type]
        self.assertEqual(len(self.db.active_ime_restores()), 1)

    async def test_zero_exit_without_actual_switch_is_retained(self) -> None:
        adb = _Adb()
        adb.current_ime = "different/.Ime"

        async def ignored_set(serial: str, *args: str, **_kwargs: object) -> AdbResult:
            adb.calls.append((serial, *args))
            if args == ("settings", "get", "secure", "default_input_method"):
                return AdbResult(adb.current_ime.encode(), b"", 0)
            return AdbResult(b"The ime set command is disabled for security reasons.", b"", 0)

        adb.shell = ignored_set  # type: ignore[method-assign]
        await restore_abandoned_imes(self.db, adb)  # type: ignore[arg-type]
        self.assertEqual(len(self.db.active_ime_restores()), 1)

    async def test_active_session_device_is_excluded(self) -> None:
        adb = _Adb()
        await restore_abandoned_imes(self.db, adb, {"dev_test"})  # type: ignore[arg-type]
        self.assertEqual(adb.calls, [])
        self.assertEqual(len(self.db.active_ime_restores()), 1)
