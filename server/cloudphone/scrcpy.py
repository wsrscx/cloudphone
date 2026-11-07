from __future__ import annotations

import asyncio
import contextlib
import secrets
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from .devices import AdbError, AdbRunner


PACKET_FLAG_SESSION = 1 << 63
PACKET_FLAG_CONFIG = 1 << 62
PACKET_FLAG_KEY_FRAME = 1 << 61
PACKET_PTS_MASK = (1 << 61) - 1
NO_PTS = PACKET_PTS_MASK

VIDEO_CODEC_H264 = 0x68323634
AUDIO_CODEC_OPUS = 0x6F707573
VIDEO_DISPATCH_BATCH_FRAMES = 1


class ScrcpyProtocolError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EncodedChunk:
    data: bytes
    pts_us: int | None
    keyframe: bool
    config: bool = False


@dataclass(frozen=True, slots=True)
class StreamGeometry:
    width: int
    height: int
    client_resize: bool = False


class ScrcpyFrameParser:
    """Incrementally parses scrcpy's 12-byte packet metadata framing."""

    def __init__(self, max_packet_size: int = 16 * 1024 * 1024):
        self.max_packet_size = max_packet_size
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[EncodedChunk | StreamGeometry]:
        self._buffer.extend(data)
        output: list[EncodedChunk | StreamGeometry] = []
        while len(self._buffer) >= 12:
            session_flags = struct.unpack_from(">I", self._buffer)[0]
            if session_flags & 0x80000000:
                _, width, height = struct.unpack_from(">III", self._buffer)
                if not (1 <= width <= 32768 and 1 <= height <= 32768):
                    raise ScrcpyProtocolError(f"invalid scrcpy stream geometry: {width}x{height}")
                output.append(StreamGeometry(width, height, bool(session_flags & 1)))
                del self._buffer[:12]
                continue
            pts_flags, size = struct.unpack_from(">QI", self._buffer)
            if size > self.max_packet_size:
                raise ScrcpyProtocolError(f"scrcpy packet exceeds {self.max_packet_size} bytes")
            if len(self._buffer) < 12 + size:
                break
            payload = bytes(self._buffer[12 : 12 + size])
            del self._buffer[: 12 + size]
            config = bool(pts_flags & PACKET_FLAG_CONFIG)
            keyframe = bool(pts_flags & PACKET_FLAG_KEY_FRAME)
            raw_pts = pts_flags & PACKET_PTS_MASK
            output.append(EncodedChunk(payload, None if config or raw_pts == NO_PTS else raw_pts, keyframe, config))
        return output


def _annexb_nal_units(data: bytes) -> list[tuple[int, bytes]]:
    starts: list[tuple[int, int]] = []
    index = 0
    while index + 3 <= len(data):
        marker_size = (
            4
            if data[index : index + 4] == b"\x00\x00\x00\x01"
            else 3
            if data[index : index + 3] == b"\x00\x00\x01"
            else 0
        )
        if marker_size:
            starts.append((index, marker_size))
            index += marker_size
        else:
            index += 1
    units: list[tuple[int, bytes]] = []
    for position, (start, marker_size) in enumerate(starts):
        nal = start + marker_size
        end = starts[position + 1][0] if position + 1 < len(starts) else len(data)
        if nal < end:
            units.append((data[nal] & 0x1F, data[start:end]))
    return units


def _annexb_nal_types(data: bytes) -> set[int]:
    return {nal_type for nal_type, _ in _annexb_nal_units(data)}


class H264ConfigCache:
    def __init__(self):
        self.codec_config = b""
        self._parameter_sets: dict[int, bytes] = {}
        self._collecting_config = False
        self._generation = 0

    @property
    def generation(self) -> int:
        return self._generation

    def process(self, chunk: EncodedChunk) -> EncodedChunk | None:
        if chunk.config:
            if not self._collecting_config:
                self._parameter_sets.clear()
            self._collecting_config = True
            units = _annexb_nal_units(chunk.data)
            parameter_sets = {
                nal_type: unit for nal_type, unit in units if nal_type in {7, 8}
            }
            if parameter_sets:
                self._parameter_sets.update(parameter_sets)
                updated = b"".join(
                    self._parameter_sets[nal_type]
                    for nal_type in (7, 8)
                    if nal_type in self._parameter_sets
                )
            else:
                updated = chunk.data
            if updated != self.codec_config:
                self.codec_config = updated
                self._generation += 1
            return None
        self._collecting_config = False
        nal_types = _annexb_nal_types(chunk.data)
        if chunk.keyframe and self.codec_config:
            if self._parameter_sets:
                prefix = b"".join(
                    self._parameter_sets[nal_type]
                    for nal_type in (7, 8)
                    if nal_type not in nal_types and nal_type in self._parameter_sets
                )
            else:
                prefix = self.codec_config if not ({7, 8} <= nal_types) else b""
            if prefix:
                return EncodedChunk(prefix + chunk.data, chunk.pts_us, True, False)
        return chunk


class ContinuousTimestamp:
    def __init__(self, default_step_us: int):
        self.default_step_us = default_step_us
        self._last_input: int | None = None
        self._last_output = 0

    def map(self, pts_us: int | None) -> int:
        if pts_us is None:
            self._last_output += self.default_step_us
        elif self._last_input is None:
            self._last_input = pts_us
        else:
            delta = max(1, min(10_000_000, pts_us - self._last_input))
            self._last_input = pts_us
            self._last_output += delta
        return self._last_output


class ScrcpyControlEncoder:
    TYPE_INJECT_KEYCODE = 0
    TYPE_INJECT_TOUCH = 2
    TYPE_ROTATE_DEVICE = 11

    @staticmethod
    def touch(action: int, pointer_id: int, x: int, y: int, width: int, height: int, pressure: int) -> bytes:
        if action not in {0, 1, 2, 3}:
            raise ValueError("invalid Android touch action")
        return struct.pack(
            ">BBQiiHHHII",
            ScrcpyControlEncoder.TYPE_INJECT_TOUCH,
            action,
            pointer_id & 0xFFFFFFFFFFFFFFFF,
            x,
            y,
            min(65535, width),
            min(65535, height),
            min(65535, max(0, pressure)),
            0,
            0,
        )

    @staticmethod
    def navigation(android_keycode: int) -> bytes:
        down = struct.pack(">BBiii", ScrcpyControlEncoder.TYPE_INJECT_KEYCODE, 0, android_keycode, 0, 0)
        up = struct.pack(">BBiii", ScrcpyControlEncoder.TYPE_INJECT_KEYCODE, 1, android_keycode, 0, 0)
        return down + up

    @staticmethod
    def rotate() -> bytes:
        return bytes([ScrcpyControlEncoder.TYPE_ROTATE_DEVICE])


ChunkCallback = Callable[[EncodedChunk], Awaitable[None] | None]
GeometryCallback = Callable[[int, int], Awaitable[None] | None]
FatalCallback = Callable[[ScrcpyProtocolError], None]


class ScrcpyTransport:
    REMOTE_JAR = "/data/local/tmp/cloudphone-scrcpy-server.jar"

    def __init__(
        self,
        adb: AdbRunner,
        serial: str,
        server_jar: Path,
        *,
        bitrate_bps: int = 6_000_000,
        max_size: int = 1280,
        max_fps: int = 60,
        audio_enabled: bool = True,
    ):
        self.adb = adb
        self.serial = serial
        self.server_jar = server_jar
        self.bitrate_bps = bitrate_bps
        self.max_size = max_size
        self.max_fps = max_fps
        self.audio_enabled = audio_enabled
        # scrcpy parses scid as a non-negative signed Java int.
        self.scid = f"{secrets.randbelow(0x7FFFFFFF) + 1:08x}"
        self.local_port: int | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.video_reader: asyncio.StreamReader | None = None
        self.video_writer: asyncio.StreamWriter | None = None
        self.audio_reader: asyncio.StreamReader | None = None
        self.audio_writer: asyncio.StreamWriter | None = None
        self.control_reader: asyncio.StreamReader | None = None
        self.control_writer: asyncio.StreamWriter | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._core_tasks: set[asyncio.Task[None]] = set()
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_buffer = bytearray()
        self._write_lock = asyncio.Lock()
        self._closing = False
        self.on_video: ChunkCallback | None = None
        self.on_audio: ChunkCallback | None = None
        self.on_geometry: GeometryCallback | None = None
        self.on_fatal: FatalCallback | None = None
        self.width = 0
        self.height = 0
        self.audio_available = False

    async def start(self) -> None:
        if not self.server_jar.is_file():
            raise FileNotFoundError(f"scrcpy server v4.1 not found: {self.server_jar}")
        await self.adb.push_if_changed(self.serial, self.server_jar, self.REMOTE_JAR)
        await self._start_once(audio_enabled=self.audio_enabled)

    async def _start_once(self, *, audio_enabled: bool) -> None:
        self._closing = False
        try:
            self.local_port = await self.adb.forward(self.serial, f"localabstract:scrcpy_{self.scid}")
            command = [
                self.adb.adb_path, "-s", self.serial, "shell",
                f"CLASSPATH={self.REMOTE_JAR}", "app_process", "/", "com.genymobile.scrcpy.Server", "4.1",
                f"scid={self.scid}", "log_level=warn", "tunnel_forward=true", "video=true",
                f"audio={str(audio_enabled).lower()}", "control=true",
                "video_codec=h264", "audio_codec=opus", f"video_bit_rate={self.bitrate_bps}", f"max_size={self.max_size}",
                f"max_fps={self.max_fps}", "video_codec_options=i-frame-interval=1",
                "clipboard_autosync=false", "cleanup=false", "send_device_meta=true",
                "send_stream_meta=true", "send_frame_meta=true", "send_dummy_byte=true",
            ]
            self.process = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
            )
            self._stderr_task = asyncio.create_task(self._capture_stderr(), name=f"scrcpy-stderr-{self.scid}")
            readers_writers = await self._connect_channels(self.local_port, 3 if audio_enabled else 2)
            if audio_enabled:
                (self.video_reader, self.video_writer), (self.audio_reader, self.audio_writer), (self.control_reader, self.control_writer) = readers_writers
            else:
                (self.video_reader, self.video_writer), (self.control_reader, self.control_writer) = readers_writers
                self.audio_reader = self.audio_writer = None
            await asyncio.wait_for(self.video_reader.readexactly(64), 3.0)
            video_codec = struct.unpack(">I", await asyncio.wait_for(self.video_reader.readexactly(4), 3.0))[0]
            if video_codec != VIDEO_CODEC_H264:
                raise ScrcpyProtocolError(f"scrcpy returned unexpected video codec id: 0x{video_codec:08x}")
            session_flags, self.width, self.height = struct.unpack(
                ">III", await asyncio.wait_for(self.video_reader.readexactly(12), 3.0)
            )
            if not session_flags & 0x80000000 or not (1 <= self.width <= 32768 and 1 <= self.height <= 32768):
                raise ScrcpyProtocolError("scrcpy returned invalid initial video stream metadata")
            audio_codec = 0
            if audio_enabled and self.audio_reader:
                audio_codec = struct.unpack(">I", await asyncio.wait_for(self.audio_reader.readexactly(4), 3.0))[0]
                if audio_codec not in {0, AUDIO_CODEC_OPUS}:
                    raise ScrcpyProtocolError(f"scrcpy returned unexpected audio codec id: 0x{audio_codec:08x}")
                self.audio_available = audio_codec == AUDIO_CODEC_OPUS
            await self._dispatch(self.on_geometry, self.width, self.height)
            video_task = asyncio.create_task(
                self._read_stream(self.video_reader, True), name=f"scrcpy-video-{self.scid}"
            )
            control_task = asyncio.create_task(self._read_control(), name=f"scrcpy-control-{self.scid}")
            process_task = asyncio.create_task(self._watch_process(), name=f"scrcpy-process-{self.scid}")
            self._tasks = [video_task, control_task, process_task]
            self._core_tasks = {video_task, control_task, process_task}
            if audio_codec == AUDIO_CODEC_OPUS:
                self._tasks.append(
                    asyncio.create_task(self._read_stream(self.audio_reader, False), name=f"scrcpy-audio-{self.scid}")
                )
            for task in self._tasks:
                task.add_done_callback(self._task_finished)
        except asyncio.CancelledError:
            await self.close()
            raise
        except (TimeoutError, asyncio.IncompleteReadError, OSError, ScrcpyProtocolError) as exc:
            await self.close()
            detail = self._stderr_detail()
            suffix = f": {detail}" if detail else f": {exc}"
            raise ScrcpyProtocolError(f"scrcpy server failed during startup{suffix}") from exc
        except BaseException:
            await self.close()
            raise

    async def _connect_channels(self, port: int, count: int) -> list[tuple[asyncio.StreamReader, asyncio.StreamWriter]]:
        deadline = asyncio.get_running_loop().time() + 8.0
        output: list[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = []
        while not output:
            if self.process and self.process.returncode is not None:
                raise ScrcpyProtocolError("scrcpy server exited before opening its device socket")
            writer: asyncio.StreamWriter | None = None
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                await asyncio.wait_for(reader.readexactly(1), 0.75)
                output.append((reader, writer))
            except (OSError, TimeoutError, asyncio.IncompleteReadError):
                if writer:
                    writer.close()
                    with contextlib.suppress(Exception):
                        await writer.wait_closed()
                if asyncio.get_running_loop().time() >= deadline:
                    raise ScrcpyProtocolError("timed out connecting to scrcpy server") from None
                await asyncio.sleep(0.1)
        try:
            while len(output) < count:
                if self.process and self.process.returncode is not None:
                    raise ScrcpyProtocolError("scrcpy server exited while opening media channels")
                try:
                    output.append(await asyncio.open_connection("127.0.0.1", port))
                except OSError:
                    if asyncio.get_running_loop().time() >= deadline:
                        raise ScrcpyProtocolError("timed out connecting to scrcpy media channels") from None
                    await asyncio.sleep(0.05)
            return output
        except BaseException:
            for _, writer in output:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
            raise

    async def _read_stream(self, reader: asyncio.StreamReader, video: bool) -> None:
        parser = ScrcpyFrameParser()
        config = H264ConfigCache() if video else None
        clock = ContinuousTimestamp(16_667 if video else 20_000)
        callback = self.on_video if video else self.on_audio
        while True:
            data = await reader.read(64 * 1024)
            if not data:
                raise ScrcpyProtocolError(f"scrcpy {'video' if video else 'audio'} stream ended")
            video_frames_since_yield = 0
            for chunk in parser.feed(data):
                if isinstance(chunk, StreamGeometry):
                    if not video:
                        raise ScrcpyProtocolError("audio stream contained video geometry metadata")
                    await self._dispatch(self.on_geometry, chunk.width, chunk.height)
                    continue
                if config:
                    processed = config.process(chunk)
                    if processed is None:
                        continue
                    chunk = processed
                elif chunk.config:
                    # Opus codec metadata is not an encoded audio frame.
                    continue
                chunk = EncodedChunk(chunk.data, clock.map(chunk.pts_us), chunk.keyframe, False)
                await self._dispatch(callback, chunk)
                if video:
                    video_frames_since_yield += 1
                    if video_frames_since_yield >= VIDEO_DISPATCH_BATCH_FRAMES:
                        video_frames_since_yield = 0
                        await asyncio.sleep(0)
            if video and video_frames_since_yield:
                # StreamReader.read() may complete synchronously while its TCP
                # buffer is non-empty. Yield once per partial batch so WebSocket
                # senders and control tasks cannot be starved by a capture burst.
                await asyncio.sleep(0)

    async def _capture_stderr(self) -> None:
        if not self.process or not self.process.stderr:
            return
        while True:
            data = await self.process.stderr.read(4096)
            if not data:
                return
            self._stderr_buffer.extend(data)
            if len(self._stderr_buffer) > 16 * 1024:
                del self._stderr_buffer[: len(self._stderr_buffer) - 16 * 1024]

    async def _watch_process(self) -> None:
        process = self.process
        if not process:
            return
        returncode = await process.wait()
        detail = self._stderr_detail()
        suffix = f": {detail}" if detail else ""
        raise ScrcpyProtocolError(f"scrcpy server exited unexpectedly with code {returncode}{suffix}")

    def _task_finished(self, task: asyncio.Task[None]) -> None:
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is None or self._closing or task not in self._core_tasks or not self.on_fatal:
            return
        reported = error if isinstance(error, ScrcpyProtocolError) else ScrcpyProtocolError(str(error))
        try:
            self.on_fatal(reported)
        except Exception as callback_error:
            asyncio.get_running_loop().call_exception_handler(
                {
                    "message": "scrcpy fatal-error callback failed",
                    "exception": callback_error,
                    "task": task,
                }
            )

    def _stderr_detail(self) -> str:
        text = self._stderr_buffer.decode("utf-8", errors="replace").strip()
        return " ".join(text.split())[-2000:]

    async def _read_control(self) -> None:
        if not self.control_reader:
            return
        try:
            while True:
                message_type = (await self.control_reader.readexactly(1))[0]
                if message_type == 0:  # Clipboard notification; disabled, but consume it defensively.
                    length = struct.unpack(">I", await self.control_reader.readexactly(4))[0]
                    if length > 16 * 1024 * 1024:
                        raise ScrcpyProtocolError("scrcpy clipboard message is too large")
                    await self.control_reader.readexactly(length)
                elif message_type == 1:  # Clipboard acknowledgement.
                    await self.control_reader.readexactly(8)
                elif message_type == 2:  # UHID output.
                    _, length = struct.unpack(">HH", await self.control_reader.readexactly(4))
                    if length > 16 * 1024:
                        raise ScrcpyProtocolError("scrcpy UHID message is too large")
                    await self.control_reader.readexactly(length)
                else:
                    raise ScrcpyProtocolError(f"unknown scrcpy device message type: {message_type}")
        except asyncio.CancelledError:
            raise
        except (asyncio.IncompleteReadError, OSError, ScrcpyProtocolError) as exc:
            error = ScrcpyProtocolError(f"scrcpy control channel ended: {exc}")
            raise error from exc

    @staticmethod
    async def _dispatch(callback: Callable[..., Awaitable[None] | None] | None, *args: object) -> None:
        if callback:
            result = callback(*args)
            if asyncio.iscoroutine(result):
                await result

    async def write_control(self, message: bytes) -> None:
        if not self.control_writer:
            raise ScrcpyProtocolError("scrcpy control channel is not connected")
        async with self._write_lock:
            self.control_writer.write(message)
            try:
                await asyncio.wait_for(self.control_writer.drain(), 0.75)
            except TimeoutError as exc:
                raise ScrcpyProtocolError("scrcpy control channel is not draining") from exc

    async def close(self) -> None:
        self._closing = True
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._core_tasks.clear()
        for writer in (self.video_writer, self.audio_writer, self.control_writer):
            if writer:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
        self.video_writer = self.audio_writer = self.control_writer = None
        self.video_reader = self.audio_reader = self.control_reader = None
        if self.process and self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.process.terminate()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.process.wait(), 3.0)
            if self.process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    self.process.kill()
                with contextlib.suppress(Exception):
                    await self.process.wait()
        if self._stderr_task:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._stderr_task, 1.0)
            self._stderr_task = None
        self.process = None
        if self.local_port is not None:
            with contextlib.suppress(Exception):
                await self.adb.remove_forward(self.serial, self.local_port)
            self.local_port = None
