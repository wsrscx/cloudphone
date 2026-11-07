from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .control import ControlOverloadError, RemoteControlRouter
from .db import Database, DeviceRecord
from .devices import AdbRunner
from .direct_stream import PACKET_VIDEO, DirectStreamClosed, DirectVideoHub, LatestDecodableQueue
from .ime import RemoteImeBridge
from .quality import (
    AdaptiveQualityController,
    EncoderCapabilities,
    QualityDecision,
    QualityMode,
    ReceiverSample,
)
from .scrcpy import EncodedChunk, ScrcpyControlEncoder, ScrcpyProtocolError, ScrcpyTransport
from .video_capabilities import probe_h264_encoder_capabilities
from .wire import EnvelopeMessage, ProtocolError, decode_envelope, encode_server_envelope


logger = logging.getLogger(__name__)


class SessionError(RuntimeError):
    pass


class ControlStreamClosed(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SessionDescription:
    id: str
    device_id: str
    client_id: str
    state: str


class BoundedControlQueue:
    """Non-blocking, bounded server-to-client control output."""

    def __init__(self, *, max_messages: int = 64, max_bytes: int = 256 * 1024):
        if max_messages < 1 or max_bytes < 1:
            raise ValueError("control queue limits must be positive")
        self.max_messages = max_messages
        self.max_bytes = max_bytes
        self.dropped_messages = 0
        self._messages: deque[bytes] = deque()
        self._bytes = 0
        self._ready = asyncio.Event()
        self._closed = False

    def push(self, message: bytes) -> bool:
        if self._closed:
            return False
        if not isinstance(message, bytes) or len(message) > self.max_bytes:
            self.dropped_messages += 1
            return False
        while self._messages and (
            len(self._messages) >= self.max_messages
            or self._bytes + len(message) > self.max_bytes
        ):
            self._bytes -= len(self._messages.popleft())
            self.dropped_messages += 1
        self._messages.append(message)
        self._bytes += len(message)
        self._ready.set()
        return True

    async def receive(self) -> bytes:
        while True:
            if self._messages:
                message = self._messages.popleft()
                self._bytes -= len(message)
                if not self._messages:
                    self._ready.clear()
                return message
            if self._closed:
                raise ControlStreamClosed
            await self._ready.wait()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._messages.clear()
        self._bytes = 0
        self._ready.set()


@dataclass(frozen=True, slots=True)
class ControlConnection:
    epoch: int
    outgoing: BoundedControlQueue


@dataclass(frozen=True, slots=True)
class VideoConnection:
    incoming: LatestDecodableQueue


TransportFactory = Callable[..., ScrcpyTransport]
ImeFactory = Callable[[Database, AdbRunner, DeviceRecord, Path], RemoteImeBridge]
StateCallback = Callable[["DirectSession", str], None]


class DirectSession:
    """A scrcpy session with isolated control and H.264 WebSocket planes."""

    MEDIA_RETRY_INITIAL_SECONDS = 0.25
    MEDIA_RETRY_MAX_SECONDS = 5.0
    RECEIVER_FREEZE_RESTART_COOLDOWN_SECONDS = 5.0

    def __init__(
        self,
        session_id: str,
        client_id: str,
        device: DeviceRecord,
        db: Database,
        adb: AdbRunner,
        scrcpy_server_path: Path,
        ime_apk_path: Path,
        initial_quality_mode: QualityMode = QualityMode.AUTO,
        encoder_capabilities: EncoderCapabilities | None = None,
        on_state_change: StateCallback | None = None,
        *,
        transport_factory: TransportFactory = ScrcpyTransport,
        ime_factory: ImeFactory = RemoteImeBridge,
    ):
        self.id = session_id
        self.client_id = client_id
        self.device = device
        self.db = db
        self.adb = adb
        self.scrcpy_server_path = scrcpy_server_path
        self.quality = AdaptiveQualityController(time.monotonic(), encoder_capabilities)
        self.quality.set_mode(initial_quality_mode, time.monotonic())
        self.ime = ime_factory(db, adb, device, ime_apk_path)
        self.video = DirectVideoHub()
        self.video.on_overload = self._on_video_overload
        self.transport: ScrcpyTransport | None = None
        self.state = "starting"
        self.width = 1
        self.height = 1
        self.rotation = 0
        self.video_generation = self.video.generation

        self._transport_factory = transport_factory
        self._on_state_change = on_state_change
        self._server_sequence = 0
        self._active_pointers: dict[int, int] = {}
        self._media_lock = asyncio.Lock()
        self._input_lock = asyncio.Lock()
        self._quality_lock = asyncio.Lock()
        self._attachment_lock = asyncio.Lock()
        self._video_attach_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._closed = False
        self._starting_transport: ScrcpyTransport | None = None
        self._media_recovery_task: asyncio.Task[None] | None = None
        self._control_reset_task: asyncio.Task[None] | None = None
        self._ime_task: asyncio.Task[None] | None = None
        self._ime_available = False
        self._had_client = False
        self._control_epoch = 0
        self._control_connection: ControlConnection | None = None
        self._video_connection: VideoConnection | None = None
        self._last_receiver_freeze_count = 0
        self._last_receiver_restart_at = float("-inf")
        self._video_ws_packets_sent = 0
        self._video_ws_frames_sent = 0
        self._video_ws_bytes_sent = 0
        self._media_restart_count = 0

        self.control = RemoteControlRouter(self._dispatch, self._on_control_worker_fatal)
        self.ime.on_focus = self._on_ime_focus

    @property
    def description(self) -> SessionDescription:
        return SessionDescription(self.id, self.device.id, self.client_id, self.state)

    @property
    def has_client(self) -> bool:
        return self._control_connection is not None or self._video_connection is not None

    async def start(self) -> None:
        self.control.start()
        try:
            await self._replace_transport(initial=True)
        except BaseException:
            await self.control.close()
            raise
        self._set_state("waiting-for-client")
        self._ime_task = asyncio.create_task(self._start_ime(), name=f"ime-start-{self.id}")

    def _make_transport(self) -> ScrcpyTransport:
        profile = self.quality.profile
        return self._transport_factory(
            self.adb,
            self.device.serial,
            self.scrcpy_server_path,
            bitrate_bps=self.quality.bitrate_bps,
            max_size=profile.max_size,
            max_fps=profile.fps,
            audio_enabled=False,
        )

    def _bind_transport(self, transport: ScrcpyTransport) -> None:
        transport.on_video = lambda chunk: self._on_video(transport, chunk)
        transport.on_audio = None
        transport.on_geometry = lambda width, height: self._on_geometry(transport, width, height)
        transport.on_fatal = lambda error: self._on_transport_fatal(transport, error)

    async def _replace_transport(
        self,
        *,
        expected: ScrcpyTransport | None = None,
        initial: bool = False,
    ) -> bool:
        async with self._media_lock:
            if self._closed:
                raise SessionError("session is closed")
            if expected is not None and self.transport is not expected:
                return False

            previous, self.transport = self.transport, None
            if previous is not None:
                await previous.close()
            if not initial:
                self.video.geometry(self.width, self.height, force_generation=True)
                self.video_generation = self.video.generation
                self._send_geometry()

            candidate = self._make_transport()
            self._starting_transport = candidate
            self._bind_transport(candidate)
            try:
                await candidate.start()
            except BaseException:
                with contextlib.suppress(Exception):
                    await candidate.close()
                raise
            finally:
                self._starting_transport = None
            self.transport = candidate
            if not initial:
                self._media_restart_count += 1
            return True

    async def _on_video(self, transport: ScrcpyTransport, chunk: EncodedChunk) -> None:
        if transport is not self.transport and transport is not self._starting_transport:
            return
        self.video.publish(chunk)

    def _on_video_overload(self) -> None:
        transport = self.transport
        if transport is None or self._closed:
            return
        # Some Android 15 encoders ignore scrcpy's I-frame interval and emit
        # only the startup IDR. Once an overloaded subscriber drops that GOP,
        # replacing the capture process is the only upstream-compatible way to
        # obtain another decodable key frame.
        self._schedule_media_recovery(
            transport,
            ScrcpyProtocolError("video output queue overloaded; restarting capture for an IDR"),
        )

    async def _on_geometry(self, transport: ScrcpyTransport, width: int, height: int) -> None:
        if transport is not self.transport and transport is not self._starting_transport:
            return
        changed = (width, height) != (self.width, self.height)
        if changed:
            # start() invokes this callback while the media lock is held. Keep
            # it non-blocking so an input write waiting for that lock cannot
            # form a media-lock/input-lock cycle during a quality restart.
            self._active_pointers.clear()
        self.width, self.height = width, height
        self.video.geometry(width, height)
        self.video_generation = self.video.generation
        self._send_geometry()
        if changed and self.quality.mode == QualityMode.ORIGINAL:
            self._send_quality_state(0, False)

    def _on_transport_fatal(self, transport: ScrcpyTransport, error: ScrcpyProtocolError) -> None:
        if self._closed or (
            transport is not self.transport and transport is not self._starting_transport
        ):
            return
        self._schedule_media_recovery(transport, error)

    def _schedule_media_recovery(
        self,
        failed_transport: ScrcpyTransport | None,
        error: Exception,
    ) -> None:
        if self._closed:
            return
        task = self._media_recovery_task
        if task is not None and not task.done():
            return
        self._send_server(
            "session_error",
            {"code": "MEDIA_RESTARTING", "message": str(error), "fatal": False},
        )
        self._refresh_state(media_recovering=True)
        self._media_recovery_task = asyncio.create_task(
            self._recover_media(failed_transport), name=f"media-recovery-{self.id}"
        )

    async def _recover_media(self, failed_transport: ScrcpyTransport | None) -> None:
        delay = self.MEDIA_RETRY_INITIAL_SECONDS
        expected = failed_transport
        try:
            while not self._closed:
                try:
                    replaced = await self._replace_transport(expected=expected)
                    if not replaced:
                        return
                    self.quality.begin_stream(time.monotonic())
                    self._send_quality_state(0, True)
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("scrcpy recovery failed for session %s: %s", self.id, exc)
                    self._send_server(
                        "session_error",
                        {"code": "MEDIA_RETRY_FAILED", "message": str(exc), "fatal": False},
                    )
                    expected = None
                    await asyncio.sleep(delay)
                    delay = min(self.MEDIA_RETRY_MAX_SECONDS, delay * 2)
        finally:
            if self._media_recovery_task is asyncio.current_task():
                self._media_recovery_task = None
            if not self._closed:
                self._refresh_state()

    async def attach_control(self) -> ControlConnection:
        async with self._attachment_lock:
            self._ensure_active()
            self._control_epoch += 1
            epoch = self._control_epoch
            previous = self._control_connection
            self._control_connection = None
            if previous is not None:
                previous.outgoing.close()
            await self._cancel_pointers()
            await self.control.reset()
            connection = ControlConnection(epoch, BoundedControlQueue())
            self._control_connection = connection
            self._had_client = True
            self._refresh_state()
            self._send_geometry()
            self._send_quality_state(0, False)
            return connection

    async def attach_video(self) -> VideoConnection:
        async with self._video_attach_lock:
            self._ensure_active()
            connection = VideoConnection(self.video.attach())
            self._video_connection = connection
            self._had_client = True
            self._refresh_state()
            try:
                await self._cancel_pointers()
                await self._replace_transport()
                self.quality.begin_stream(time.monotonic())
                self._send_quality_state(0, True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._closed:
                    raise SessionError("session is no longer active") from exc
                self._schedule_media_recovery(None, exc)
            return connection

    def record_video_sent(self, packet_kind: int, encoded_size: int) -> None:
        self._video_ws_packets_sent += 1
        self._video_ws_bytes_sent += max(0, encoded_size)
        if packet_kind == PACKET_VIDEO:
            self._video_ws_frames_sent += 1

    @property
    def stream_metrics(self) -> dict[str, int]:
        return {
            "framesCaptured": self.video.frames_captured,
            "bytesCaptured": self.video.bytes_captured,
            "framesPublished": self.video.frames_published,
            "bytesPublished": self.video.bytes_published,
            "videoWsPacketsSent": self._video_ws_packets_sent,
            "videoWsFramesSent": self._video_ws_frames_sent,
            "videoWsBytesSent": self._video_ws_bytes_sent,
            "queueDroppedFrames": self.video.queue_dropped_frames,
            "queueOverloads": self.video.overload_count,
            "mediaRestarts": self._media_restart_count,
        }

    async def detach(self, connection: ControlConnection | VideoConnection) -> None:
        if isinstance(connection, ControlConnection):
            async with self._attachment_lock:
                if self._control_connection is not connection:
                    connection.outgoing.close()
                    return
                self._control_connection = None
                connection.outgoing.close()
                await self._cancel_pointers()
                self._refresh_state()
            return
        if self._video_connection is connection:
            self._video_connection = None
            self.video.detach(connection.incoming)
            self._refresh_state()
        else:
            connection.incoming.close()

    async def receive_control(self, data: bytes, epoch: int) -> bool:
        connection = self._control_connection
        if connection is None or connection.epoch != epoch:
            raise ControlStreamClosed("control connection was superseded")
        try:
            envelope = decode_envelope(data)
            if envelope.payload_type == "touch_moves":
                channel = "motion.v2"
            elif envelope.payload_type in RemoteControlRouter.INPUT_TYPES:
                channel = "input.v2"
            else:
                channel = "control.v2"
            return self.control.submit(data, channel)
        except ControlOverloadError as exc:
            self._send_server(
                "session_error",
                {"code": "CONTROL_OVERLOAD", "message": str(exc), "fatal": False},
            )
            await self._cancel_pointers()
            await self.control.reset()
            return False
        except ProtocolError as exc:
            self._send_server(
                "session_error",
                {"code": "INVALID_CONTROL_MESSAGE", "message": str(exc), "fatal": False},
            )
            return False

    async def _dispatch(self, envelope: EnvelopeMessage) -> None:
        if envelope.payload_type in {"touch", "touch_moves"} and (
            envelope.video_generation != self.video_generation
        ):
            return
        payload = envelope.payload
        if envelope.payload_type == "touch":
            async with self._input_lock:
                point = payload["point"]
                action_map = {1: 0, 2: 1, 3: 3}
                pointer_id = int(point["pointerId"])
                action = action_map[int(payload["action"])]
                messages: list[bytes] = []
                if action == 0:
                    if pointer_id not in self._active_pointers and len(self._active_pointers) >= 10:
                        return
                    if pointer_id in self._active_pointers:
                        messages.append(self._encode_touch(3, point, pressure=0))
                    self._active_pointers[pointer_id] = envelope.sequence
                elif pointer_id not in self._active_pointers:
                    return
                else:
                    self._active_pointers.pop(pointer_id, None)
                messages.append(self._encode_touch(action, point))
                await self._write_transport_control(b"".join(messages))
        elif envelope.payload_type == "touch_moves":
            async with self._input_lock:
                messages = [
                    self._encode_touch(2, point)
                    for point in payload["points"]
                    if envelope.sequence
                    > self._active_pointers.get(int(point["pointerId"]), envelope.sequence)
                ]
                if messages:
                    await self._write_transport_control(b"".join(messages))
        elif envelope.payload_type == "navigation":
            async with self._input_lock:
                keycode = {1: 4, 2: 3, 3: 187}[int(payload["key"])]
                await self._write_transport_control(ScrcpyControlEncoder.navigation(keycode))
        elif envelope.payload_type == "rotate":
            async with self._input_lock:
                messages = self._cancel_pointer_messages()
                messages.append(ScrcpyControlEncoder.rotate())
                await self._write_transport_control(b"".join(messages))
        elif envelope.payload_type == "quality_request":
            await self._handle_quality_request(
                QualityMode(int(payload["mode"])), int(payload["requestId"])
            )
        elif envelope.payload_type == "receiver_stats":
            await self._handle_receiver_stats(
                ReceiverSample(
                    rtt_ms=float(payload["rttMs"]),
                    packet_loss=float(payload["packetLoss"]),
                    jitter_ms=float(payload["jitterMs"]),
                    freeze_count=int(payload["freezeCount"]),
                    available_bitrate_bps=int(payload["availableBitrateBps"]),
                    rendered_fps=float(payload["renderedFps"]),
                    frames_dropped=int(payload["framesDropped"]),
                    playout_delay_ms=float(payload["playoutBufferMs"]),
                    incoming_bitrate_bps=int(payload["incomingBitrateBps"]),
                )
            )
        elif envelope.payload_type == "ime_submit" and self._ime_available:
            try:
                await self.ime.submit(str(payload["text"]), int(payload["editorAction"]))
            except Exception as exc:
                self._send_server(
                    "session_error",
                    {"code": "IME_FAILED", "message": str(exc), "fatal": False},
                )

    async def _write_transport_control(self, message: bytes) -> bool:
        failed: ScrcpyTransport | None = None
        error: Exception | None = None
        async with self._media_lock:
            transport = self.transport
            if transport is None:
                return False
            try:
                await transport.write_control(message)
                return True
            except Exception as exc:
                failed, error = transport, exc
        assert error is not None
        self._schedule_media_recovery(failed, error)
        return False

    def _encode_touch(
        self,
        action: int,
        point: dict[str, object],
        *,
        pressure: int | None = None,
    ) -> bytes:
        x = round(int(point["x"]) * max(0, self.width - 1) / 65535)
        y = round(int(point["y"]) * max(0, self.height - 1) / 65535)
        return ScrcpyControlEncoder.touch(
            action,
            int(point["pointerId"]),
            x,
            y,
            self.width,
            self.height,
            int(point["pressure"]) if pressure is None else pressure,
        )

    async def _cancel_pointers(self) -> None:
        async with self._input_lock:
            messages = self._cancel_pointer_messages()
            if messages:
                await self._write_transport_control(b"".join(messages))

    def _cancel_pointer_messages(self) -> list[bytes]:
        messages = [
            ScrcpyControlEncoder.touch(3, pointer_id, 0, 0, self.width, self.height, 0)
            for pointer_id in self._active_pointers
        ]
        self._active_pointers.clear()
        return messages

    async def _handle_quality_request(self, mode: QualityMode, request_id: int) -> None:
        async with self._quality_lock:
            previous = self.quality.snapshot()
            decision = self.quality.set_mode(mode, time.monotonic())
            if await self._apply_quality(decision, request_id):
                return
            self.quality.restore(previous)
            self._send_quality_state(request_id, False)
            self._schedule_media_recovery(None, ScrcpyProtocolError("quality restart failed"))

    async def _handle_receiver_stats(self, sample: ReceiverSample) -> None:
        async with self._quality_lock:
            now = time.monotonic()
            if sample.freeze_count < self._last_receiver_freeze_count:
                # A re-created Android client starts its cumulative counter at
                # zero; treat that as a new baseline, not as an old freeze.
                self._last_receiver_freeze_count = sample.freeze_count
                freeze_increased = False
            else:
                freeze_increased = sample.freeze_count > self._last_receiver_freeze_count
                self._last_receiver_freeze_count = sample.freeze_count
            previous = self.quality.snapshot()
            decision = self.quality.observe(sample, now)
            if decision is not None:
                if await self._apply_quality(decision, 0):
                    if freeze_increased:
                        self._last_receiver_restart_at = now
                    return
                self.quality.restore(previous)
                self._send_quality_state(0, False)
                self._schedule_media_recovery(
                    None, ScrcpyProtocolError("adaptive restart failed")
                )
                return

            if (
                not freeze_increased
                or self._video_connection is None
                or now - self._last_receiver_restart_at
                < self.RECEIVER_FREEZE_RESTART_COOLDOWN_SECONDS
            ):
                return
            self._last_receiver_restart_at = now
            await self._cancel_pointers()
            try:
                await self._replace_transport()
            except Exception as exc:
                self._send_server(
                    "session_error",
                    {"code": "RECEIVER_FREEZE", "message": str(exc), "fatal": False},
                )
                self._schedule_media_recovery(None, exc)
                return
            self._send_quality_state(0, True)

    async def _apply_quality(self, decision: QualityDecision, request_id: int) -> bool:
        del decision
        await self._cancel_pointers()
        try:
            await self._replace_transport()
        except Exception as exc:
            self._send_server(
                "session_error",
                {"code": "QUALITY_APPLY_FAILED", "message": str(exc), "fatal": False},
            )
            return False
        self._send_quality_state(request_id, True)
        return True

    async def _start_ime(self) -> None:
        try:
            await self.ime.start()
            if self._closed:
                await self.ime.close()
                return
            self._ime_available = True
            self.db.update_device(self.device.id, assistant_ready=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            with contextlib.suppress(Exception):
                await self.ime.close()
            self._ime_available = False
            with contextlib.suppress(Exception):
                self.db.update_device(self.device.id, assistant_ready=False)

    async def _on_ime_focus(self, focus: dict[str, object]) -> None:
        self._send_server("ime_focus", focus)

    def _on_control_worker_fatal(self, error: Exception) -> None:
        if self._closed:
            return
        self._send_server(
            "session_error",
            {"code": "CONTROL_WORKER_RESTARTING", "message": str(error), "fatal": False},
        )
        task = self._control_reset_task
        if task is None or task.done():
            self._control_reset_task = asyncio.create_task(
                self._reset_control_worker(), name=f"control-recovery-{self.id}"
            )

    async def _reset_control_worker(self) -> None:
        try:
            await self._cancel_pointers()
            await self.control.reset()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("control worker recovery failed for session %s", self.id)
        finally:
            if self._control_reset_task is asyncio.current_task():
                self._control_reset_task = None

    def _send_geometry(self) -> None:
        self._send_server(
            "video_geometry",
            {"width": self.width, "height": self.height, "rotation": self.rotation},
        )

    def _send_quality_state(self, request_id: int, encoder_restarted: bool) -> None:
        profile = self.quality.profile
        width, height = (
            (self.width, self.height)
            if profile.mode == QualityMode.ORIGINAL
            else (profile.width, profile.height)
        )
        self._send_server(
            "quality_applied",
            {
                "requestId": request_id,
                "mode": int(self.quality.mode),
                "width": width,
                "height": height,
                "fps": profile.fps,
                "bitrateBps": self.quality.bitrate_bps,
                "encoderRestarted": encoder_restarted,
            },
        )

    def _send_server(self, payload_type: str, payload: dict[str, object]) -> None:
        connection = self._control_connection
        if connection is None:
            return
        self._server_sequence += 1
        connection.outgoing.push(
            encode_server_envelope(
                self._server_sequence, self.video_generation, payload_type, payload
            )
        )

    def _ensure_active(self) -> None:
        if self._closed or self.state in {"closing", "closed"}:
            raise SessionError("session is no longer active")

    def _refresh_state(self, *, media_recovering: bool = False) -> None:
        if self._closed:
            return
        recovering = media_recovering or (
            self._media_recovery_task is not None and not self._media_recovery_task.done()
        )
        if not self.has_client:
            state = "reconnecting" if self._had_client else "waiting-for-client"
        elif recovering:
            state = "media-recovering"
        else:
            state = "connected"
        self._set_state(state)

    def _set_state(self, state: str) -> None:
        if self.state == state:
            return
        self.state = state
        if self._on_state_change is not None:
            self._on_state_change(self, state)

    async def close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            self._set_state("closing")
            cleanup = asyncio.create_task(self._close_resources(), name=f"session-close-{self.id}")
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise
            finally:
                self._set_state("closed")

    async def _close_resources(self) -> None:
        control_connection, self._control_connection = self._control_connection, None
        if control_connection is not None:
            control_connection.outgoing.close()
        video_connection, self._video_connection = self._video_connection, None
        if video_connection is not None:
            self.video.detach(video_connection.incoming)
        self.video.close()

        for task in (self._media_recovery_task, self._control_reset_task, self._ime_task):
            if task is not None and task is not asyncio.current_task():
                task.cancel()
        pending = [
            task
            for task in (self._media_recovery_task, self._control_reset_task, self._ime_task)
            if task is not None and task is not asyncio.current_task()
        ]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._media_recovery_task = None
        self._control_reset_task = None
        self._ime_task = None

        await self.control.close()
        transport, self.transport = self.transport, None
        await asyncio.gather(
            transport.close() if transport is not None else asyncio.sleep(0),
            self.ime.close(),
            return_exceptions=True,
        )
        self._ime_available = False
        with contextlib.suppress(Exception):
            self.db.update_device(self.device.id, assistant_ready=False)


class SessionManager:
    INITIAL_CLIENT_TIMEOUT_SECONDS = 20.0
    RECONNECT_TIMEOUT_SECONDS = 30.0
    TERMINAL_STATES = {"closing", "closed"}

    def __init__(
        self,
        db: Database,
        adb: AdbRunner,
        scrcpy_server_path: Path,
        ime_apk_path: Path,
    ):
        self.db = db
        self.adb = adb
        self.scrcpy_server_path = scrcpy_server_path
        self.ime_apk_path = ime_apk_path
        self._current: DirectSession | None = None
        self._lock = asyncio.Lock()
        self._startup_timeout_task: asyncio.Task[None] | None = None
        self._reconnect_timeout_task: asyncio.Task[None] | None = None
        self._encoder_capabilities: dict[str, EncoderCapabilities] = {}

    @property
    def current(self) -> DirectSession | None:
        return self._current

    async def create(
        self,
        device_id: str,
        client_id: str,
        quality_mode: QualityMode = QualityMode.AUTO,
    ) -> DirectSession:
        async with self._lock:
            current = self._current
            if current and current.state not in self.TERMINAL_STATES:
                if current.client_id == client_id and current.device.id == device_id:
                    return current
                raise SessionError("another device session is already active")
            if current is not None:
                await self._close_current_locked(current)

            device = self.db.get_device(device_id)
            if not device or not device.authorized:
                raise SessionError("device is not authorized")
            if device.state != "device":
                raise SessionError("device is offline or not authorized by ADB")

            capabilities = self._encoder_capabilities.get(device.serial)
            if capabilities is None:
                try:
                    capabilities = await probe_h264_encoder_capabilities(self.adb, device.serial)
                except Exception:
                    capabilities = EncoderCapabilities()
                self._encoder_capabilities[device.serial] = capabilities

            session = DirectSession(
                f"ses_{secrets.token_urlsafe(24)}",
                client_id,
                device,
                self.db,
                self.adb,
                self.scrcpy_server_path,
                self.ime_apk_path,
                initial_quality_mode=quality_mode,
                encoder_capabilities=capabilities,
                on_state_change=self._session_state_changed,
            )
            self._current = session
            try:
                await session.start()
            except BaseException:
                try:
                    await session.close()
                finally:
                    if self._current is session:
                        self._current = None
                raise
            self._schedule_timeout(
                "_startup_timeout_task",
                session,
                self.INITIAL_CLIENT_TIMEOUT_SECONDS,
                "startup",
            )
            return session

    def _session_state_changed(self, session: DirectSession, state: str) -> None:
        if self._current is not session:
            return
        if state in {"connected", "media-recovering"} and session.has_client:
            self._cancel_timeout("_startup_timeout_task")
            self._cancel_timeout("_reconnect_timeout_task")
        elif state == "reconnecting":
            self._schedule_timeout(
                "_reconnect_timeout_task",
                session,
                self.RECONNECT_TIMEOUT_SECONDS,
                "reconnect",
            )

    def _schedule_timeout(
        self,
        attribute: str,
        session: DirectSession,
        delay: float,
        purpose: str,
    ) -> None:
        self._cancel_timeout(attribute)

        async def expire() -> None:
            if delay:
                await asyncio.sleep(delay)
            await self.end(session.id, session.client_id)

        task = asyncio.create_task(expire(), name=f"{purpose}-timeout-{session.id}")
        setattr(self, attribute, task)
        task.add_done_callback(lambda done: self._timeout_finished(attribute, done))

    def _timeout_finished(self, attribute: str, task: asyncio.Task[None]) -> None:
        if getattr(self, attribute) is task:
            setattr(self, attribute, None)
        try:
            task.exception()
        except asyncio.CancelledError:
            pass

    def _cancel_timeout(self, attribute: str) -> None:
        task = getattr(self, attribute)
        setattr(self, attribute, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def _cancel_timeouts(self) -> None:
        self._cancel_timeout("_startup_timeout_task")
        self._cancel_timeout("_reconnect_timeout_task")

    def get(self, session_id: str, client_id: str) -> DirectSession | None:
        session = self._current
        return (
            session
            if session is not None
            and session.id == session_id
            and session.client_id == client_id
            and session.state not in self.TERMINAL_STATES
            else None
        )

    async def end(self, session_id: str, client_id: str) -> bool:
        async with self._lock:
            session = self._current
            if session is None or session.id != session_id or session.client_id != client_id:
                return False
            await self._close_current_locked(session)
            return True

    async def _close_current_locked(self, session: DirectSession) -> None:
        self._cancel_timeouts()
        try:
            await session.close()
        finally:
            if self._current is session:
                self._current = None

    async def device_snapshot_changed(self, devices: list[DeviceRecord]) -> None:
        session = self._current
        if session is None:
            return
        current = next((device for device in devices if device.id == session.device.id), None)
        if current is None or not current.authorized or current.state != "device":
            await self.end(session.id, session.client_id)

    async def revoke_device(self, device_id: str) -> None:
        session = self._current
        if session is not None and session.device.id == device_id:
            await self.end(session.id, session.client_id)

    async def shutdown(self) -> None:
        session = self._current
        if session is not None:
            await self.end(session.id, session.client_id)


__all__ = [
    "BoundedControlQueue",
    "ControlConnection",
    "ControlStreamClosed",
    "DirectSession",
    "DirectStreamClosed",
    "SessionDescription",
    "SessionError",
    "SessionManager",
    "VideoConnection",
]
