from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from .db import Database, DeviceRecord


class AdbError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AdbResult:
    stdout: bytes
    stderr: bytes
    returncode: int

    @property
    def text(self) -> str:
        return self.stdout.decode("utf-8", errors="replace").strip()


class AdbRunner:
    """ADB adapter whose operations are all fixed by server code.

    Public API payloads are never accepted as command fragments. Arguments are
    passed directly to CreateProcess/execve, never through a command shell.
    """

    def __init__(self, adb_path: str | Path):
        self.adb_path = str(adb_path)
        self._artifact_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._installed_artifacts: dict[tuple[str, str], str] = {}
        self._verified_artifacts: dict[tuple[str, str], str] = {}

    async def run(self, *args: str, timeout: float = 20.0, check: bool = True) -> AdbResult:
        process = await asyncio.create_subprocess_exec(
            self.adb_path,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        except asyncio.CancelledError:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                with contextlib.suppress(Exception):
                    await process.wait()
            raise
        except TimeoutError:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                with contextlib.suppress(Exception):
                    await process.wait()
            raise AdbError(f"ADB operation timed out: {args[0] if args else 'command'}") from None
        result = AdbResult(stdout, stderr, process.returncode or 0)
        if check and result.returncode:
            detail = stderr.decode("utf-8", errors="replace").strip()[-500:]
            raise AdbError(f"ADB {args[0] if args else 'command'} failed: {detail}")
        return result

    async def shell(self, serial: str, *args: str, timeout: float = 20.0, check: bool = True) -> AdbResult:
        return await self.run("-s", serial, "shell", *args, timeout=timeout, check=check)

    async def push(self, serial: str, local: Path, remote: str) -> None:
        await self.run("-s", serial, "push", str(local), remote, timeout=60.0)

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    async def push_if_changed(self, serial: str, local: Path, remote: str) -> bool:
        """Push an artifact only when the remote bytes do not already match."""
        digest = await asyncio.to_thread(self._file_sha256, local)
        key = (serial, remote)
        lock = self._artifact_locks.setdefault(key, asyncio.Lock())
        async with lock:
            remote_digest = await self.shell(serial, "sha256sum", remote, timeout=8.0, check=False)
            if remote_digest.returncode == 0 and remote_digest.text.split(maxsplit=1)[:1] == [digest]:
                self._verified_artifacts[key] = digest
                return False
            await self.push(serial, local, remote)
            self._verified_artifacts[key] = digest
            return True

    async def remote_artifact_matches(self, serial: str, local: Path, remote: str) -> bool:
        """Verify a provisioned remote artifact without modifying the device."""
        digest = await asyncio.to_thread(self._file_sha256, local)
        key = (serial, remote)
        lock = self._artifact_locks.setdefault(key, asyncio.Lock())
        async with lock:
            if self._verified_artifacts.get(key) == digest:
                return True
            remote_digest = await self.shell(serial, "sha256sum", remote, timeout=8.0, check=False)
            matches = remote_digest.returncode == 0 and remote_digest.text.split(maxsplit=1)[:1] == [digest]
            if matches:
                self._verified_artifacts[key] = digest
            else:
                self._verified_artifacts.pop(key, None)
            return matches

    async def install(self, serial: str, apk: Path) -> None:
        await self.run("-s", serial, "install", "-r", str(apk), timeout=120.0)

    @staticmethod
    def _base_apk_path(pm_path_output: str) -> str | None:
        paths: list[str] = []
        for raw_line in pm_path_output.splitlines():
            line = raw_line.strip()
            if not line.startswith("package:"):
                continue
            path = line.removeprefix("package:").strip()
            if path.startswith("/") and path.endswith(".apk"):
                paths.append(path)
        fallback = paths[0] if len(paths) == 1 else None
        return next((path for path in paths if path.rsplit("/", 1)[-1] == "base.apk"), fallback)

    async def install_if_changed(self, serial: str, apk: Path, package_name: str) -> bool:
        """Install an APK only when the device's installed base APK differs."""
        digest = await asyncio.to_thread(self._file_sha256, apk)
        key = (serial, package_name)
        lock = self._artifact_locks.setdefault(key, asyncio.Lock())
        async with lock:
            installed = await self.shell(serial, "pm", "path", package_name, timeout=8.0, check=False)
            base_apk = self._base_apk_path(installed.text) if installed.returncode == 0 else None
            if base_apk:
                if self._installed_artifacts.get(key) == digest:
                    return False

                remote_digest = await self.shell(serial, "sha256sum", base_apk, timeout=8.0, check=False)
                if remote_digest.returncode == 0 and remote_digest.text.split(maxsplit=1)[:1] == [digest]:
                    self._installed_artifacts[key] = digest
                    return False
            await self.install(serial, apk)
            self._installed_artifacts[key] = digest
            return True

    async def forward(self, serial: str, remote: str) -> int:
        result = await self.run("-s", serial, "forward", "tcp:0", remote)
        try:
            return int(result.text)
        except ValueError as exc:
            raise AdbError("ADB did not return an allocated forwarding port") from exc

    async def remove_forward(self, serial: str, local_port: int) -> None:
        await self.run("-s", serial, "forward", "--remove", f"tcp:{local_port}", check=False)


@dataclass(frozen=True, slots=True)
class DiscoveredDevice:
    serial: str
    state: str
    model: str


_PROPERTY = re.compile(r"(?P<key>[A-Za-z0-9_.-]+):(?P<value>[^\s]+)")


def parse_adb_device_lines(lines: list[str]) -> list[DiscoveredDevice]:
    devices: list[DiscoveredDevice] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("List of devices attached") or line.startswith("* daemon"):
            continue
        columns = line.split()
        if len(columns) < 2:
            continue
        serial, state = columns[0], columns[1]
        properties = {match.group("key"): match.group("value") for match in _PROPERTY.finditer(line)}
        devices.append(DiscoveredDevice(serial, state, properties.get("model", "").replace("_", " ")))
    return devices


ChangeListener = Callable[[list[DeviceRecord]], Awaitable[None] | None]


class DeviceTracker:
    def __init__(self, db: Database, adb: AdbRunner, identity_secret: bytes):
        self.db = db
        self.adb = adb
        self.identity_secret = identity_secret
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._listeners: list[ChangeListener] = []

    def device_id(self, serial: str) -> str:
        digest = hmac.new(self.identity_secret, b"device-id\0" + serial.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"dev_{digest[:32]}"

    def add_listener(self, listener: ChangeListener) -> None:
        self._listeners.append(listener)

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping.clear()
            self._task = asyncio.create_task(self._run(), name="adb-device-tracker")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def refresh_once(self) -> list[DeviceRecord]:
        result = await self.adb.run("devices", "-l", timeout=10.0)
        devices = parse_adb_device_lines(result.text.splitlines())
        return await self._apply_snapshot(devices)

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                process = await asyncio.create_subprocess_exec(
                    self.adb.adb_path,
                    "track-devices",
                    "-l",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                assert process.stdout is not None
                snapshot: list[str] = []
                while not self._stopping.is_set():
                    raw = await process.stdout.readline()
                    if not raw:
                        break
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    if line:
                        snapshot.append(line)
                    elif snapshot:
                        await self._apply_snapshot(parse_adb_device_lines(snapshot))
                        snapshot.clear()
                if snapshot:
                    await self._apply_snapshot(parse_adb_device_lines(snapshot))
                if process.returncode is None:
                    process.terminate()
                    await process.wait()
            except (OSError, AdbError):
                pass
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=2.0)
            except TimeoutError:
                try:
                    await self.refresh_once()
                except (OSError, AdbError):
                    self.db.mark_missing_offline(set())

    async def _apply_snapshot(self, discovered: list[DiscoveredDevice]) -> list[DeviceRecord]:
        present = {item.serial for item in discovered}
        output: list[DeviceRecord] = []
        for item in discovered:
            android_version = ""
            if item.state == "device":
                try:
                    android_version = (await self.adb.shell(item.serial, "getprop", "ro.build.version.release", timeout=4.0)).text
                except AdbError:
                    pass
            output.append(self.db.upsert_device(self.device_id(item.serial), item.serial, item.state, item.model, android_version))
        self.db.mark_missing_offline(present)
        snapshot = self.db.list_devices()
        for listener in tuple(self._listeners):
            result = listener(snapshot)
            if asyncio.iscoroutine(result):
                await result
        return output
