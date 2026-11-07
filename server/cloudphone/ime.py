from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import secrets
import struct
from pathlib import Path
from typing import Awaitable, Callable

from .db import Database, DeviceRecord
from .devices import AdbError, AdbRunner


IME_PACKAGE = "com.cloudphone.ime"
IME_COMPONENT = f"{IME_PACKAGE}/.RemoteInputMethodService"
MAX_IME_FRAME = 32 * 1024
IME_DISCOVERY_TIMEOUT_SECONDS = 8.0


class ImeBridgeError(RuntimeError):
    pass


FocusCallback = Callable[[dict[str, object]], Awaitable[None] | None]


class RemoteImeBridge:
    def __init__(self, db: Database, adb: AdbRunner, device: DeviceRecord, apk_path: Path):
        self.db = db
        self.adb = adb
        self.device = device
        self.apk_path = apk_path
        self.socket_name = f"cloudphone_ime_{secrets.token_hex(12)}"
        self.auth_token = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
        self.previous_ime = ""
        self.local_port: int | None = None
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._configured = False
        self.on_focus: FocusCallback | None = None

    async def start(self) -> None:
        try:
            if not self.apk_path.is_file():
                raise FileNotFoundError(f"Remote IME APK not found: {self.apk_path}")
            await self.adb.install_if_changed(self.device.serial, self.apk_path, IME_PACKAGE)
            await self._wait_until_discoverable()
            self.previous_ime = await self._current_ime()
            self.db.save_ime_restore(self.device.id, self.previous_ime, self.socket_name)
            await self.adb.shell(
                self.device.serial, "am", "broadcast", "-a", "com.cloudphone.ime.CONFIGURE", "-n",
                "com.cloudphone.ime/.SocketConfigReceiver", "--es", "socket", self.socket_name,
                "--es", "token", self.auth_token,
            )
            self._configured = True
            await self.adb.shell(self.device.serial, "ime", "enable", IME_COMPONENT, check=False)
            await self._select_ime(IME_COMPONENT)
            self.local_port = await self.adb.forward(self.device.serial, f"localabstract:{self.socket_name}")
            deadline = asyncio.get_running_loop().time() + 5.0
            while True:
                try:
                    self.reader, self.writer = await asyncio.open_connection("127.0.0.1", self.local_port)
                    break
                except OSError:
                    if asyncio.get_running_loop().time() >= deadline:
                        raise ImeBridgeError("timed out connecting to Remote IME helper") from None
                    await asyncio.sleep(0.1)
            await self._write_frame({"type": "auth", "token": self.auth_token})
            authentication = await asyncio.wait_for(self._read_frame(), timeout=3.0)
            if authentication.get("type") != "auth_result" or authentication.get("accepted") is not True:
                raise ImeBridgeError("Remote IME helper rejected the session token")
            self._reader_task = asyncio.create_task(self._read_events(), name=f"ime-events-{self.device.id}")
        except BaseException:
            await self.close()
            raise

    async def _wait_until_discoverable(self) -> None:
        deadline = asyncio.get_running_loop().time() + IME_DISCOVERY_TIMEOUT_SECONDS
        while True:
            result = await self.adb.shell(self.device.serial, "ime", "list", "-a", "-s", check=False)
            if IME_COMPONENT in result.text:
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise ImeBridgeError("Remote IME was installed but Android did not register its input-method component")
            await asyncio.sleep(0.2)

    async def _current_ime(self) -> str:
        return (await self.adb.shell(
            self.device.serial, "settings", "get", "secure", "default_input_method"
        )).text

    async def _select_ime(self, component: str) -> None:
        result = await self.adb.shell(self.device.serial, "ime", "set", component, check=False)
        if await self._current_ime() == component:
            return
        detail = (result.stdout + result.stderr).decode("utf-8", errors="replace").strip()[-300:]
        suffix = f": {detail}" if detail else ""
        raise ImeBridgeError(f"Android refused to select Remote IME{suffix}")

    async def _read_frame(self) -> dict[str, object]:
        if not self.reader:
            raise ImeBridgeError("Remote IME helper is not connected")
        size = struct.unpack(">I", await self.reader.readexactly(4))[0]
        if not 1 <= size <= MAX_IME_FRAME:
            raise ImeBridgeError("invalid Remote IME frame size")
        try:
            value = json.loads((await self.reader.readexactly(size)).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ImeBridgeError("invalid Remote IME event") from exc
        if not isinstance(value, dict):
            raise ImeBridgeError("Remote IME event must be a JSON object")
        return value

    async def _write_frame(self, value: dict[str, object]) -> None:
        if not self.writer:
            raise ImeBridgeError("Remote IME helper is not connected")
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if not 1 <= len(payload) <= MAX_IME_FRAME:
            raise ImeBridgeError("Remote IME frame is too large")
        async with self._write_lock:
            self.writer.write(struct.pack(">I", len(payload)) + payload)
            await self.writer.drain()

    async def _read_events(self) -> None:
        while True:
            event = await self._read_frame()
            if event.get("type") != "focus":
                continue
            focus = {
                "active": bool(event.get("active")),
                "inputType": int(event.get("inputType", 0)),
                "imeOptions": int(event.get("imeOptions", 0)),
                "password": bool(event.get("password")),
            }
            if self.on_focus:
                result = self.on_focus(focus)
                if asyncio.iscoroutine(result):
                    await result

    async def submit(self, text: str, editor_action: int) -> None:
        encoded_text = text.encode("utf-8")
        if len(encoded_text) > 16_384:
            raise ImeBridgeError("IME text exceeds 16 KiB")
        # Input text is intentionally never logged.
        await self._write_frame({"type": "commit", "text": text, "editorAction": int(editor_action)})

    async def close(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
            self._reader_task = None
        if self.writer:
            self.writer.close()
            with contextlib.suppress(Exception):
                await self.writer.wait_closed()
            self.writer = None
        if self.local_port is not None:
            await self.adb.remove_forward(self.device.serial, self.local_port)
            self.local_port = None
        if self._configured:
            with contextlib.suppress(AdbError, OSError):
                await self.adb.shell(
                    self.device.serial, "am", "broadcast", "-a", "com.cloudphone.ime.CLEAR", "-n",
                    "com.cloudphone.ime/.SocketConfigReceiver", check=False,
                )
            self._configured = False
        restored = not self.previous_ime or self.previous_ime in {"null", IME_COMPONENT}
        if self.previous_ime and self.previous_ime not in {"null", IME_COMPONENT}:
            try:
                if await self._current_ime() != self.previous_ime:
                    await self.adb.shell(self.device.serial, "ime", "set", self.previous_ime, check=False)
                restored = await self._current_ime() == self.previous_ime
            except (AdbError, OSError):
                restored = False
        if restored:
            self.db.finish_ime_restore(self.device.id)


async def restore_abandoned_imes(
    db: Database,
    adb: AdbRunner,
    excluded_device_ids: set[str] | None = None,
) -> None:
    excluded_device_ids = excluded_device_ids or set()
    for restore in db.active_ime_restores():
        device_id = str(restore["device_id"])
        if device_id in excluded_device_ids:
            continue
        device = db.get_device(device_id)
        previous = str(restore["previous_ime"])
        if not previous or previous in {"null", IME_COMPONENT}:
            db.finish_ime_restore(device_id)
            continue
        if device and device.state == "device" and previous and previous not in {"null", IME_COMPONENT}:
            try:
                current = (await adb.shell(
                    device.serial, "settings", "get", "secure", "default_input_method"
                )).text
                if current != previous:
                    await adb.shell(device.serial, "ime", "set", previous, check=False)
                    current = (await adb.shell(
                        device.serial, "settings", "get", "secure", "default_input_method"
                    )).text
            except (AdbError, OSError):
                continue
            if current == previous:
                db.finish_ime_restore(device.id)
