from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from typing import Callable

from cloudphone.db import DeviceRecord
from cloudphone.direct_session import (
    BoundedControlQueue,
    ControlStreamClosed,
    DirectSession,
)
from cloudphone.direct_stream import PACKET_RESET, PACKET_VIDEO
from cloudphone.quality import QualityMode, ReceiverSample
from cloudphone.scrcpy import EncodedChunk, ScrcpyProtocolError


class _Database:
    def __init__(self) -> None:
        self.updates: list[tuple[str, dict[str, object]]] = []

    def update_device(self, device_id: str, **values: object) -> None:
        self.updates.append((device_id, values))


class _Ime:
    def __init__(self) -> None:
        self.on_focus: Callable[..., object] | None = None
        self.closed = False

    async def start(self) -> None:
        return

    async def submit(self, _text: str, _action: int) -> None:
        return

    async def close(self) -> None:
        self.closed = True


class _Transport:
    def __init__(self, *args: object, fail_start: bool = False, **kwargs: object):
        self.args = args
        self.kwargs = kwargs
        self.fail_start = fail_start
        self.closed = False
        self.writes: list[bytes] = []
        self.on_video: Callable[[EncodedChunk], object] | None = None
        self.on_audio: Callable[[EncodedChunk], object] | None = None
        self.on_geometry: Callable[[int, int], object] | None = None
        self.on_fatal: Callable[[ScrcpyProtocolError], object] | None = None

    async def start(self) -> None:
        if self.fail_start:
            raise ScrcpyProtocolError("simulated encoder startup failure")
        if self.on_geometry is not None:
            result = self.on_geometry(1080, 1920)
            if asyncio.iscoroutine(result):
                await result

    async def write_control(self, message: bytes) -> None:
        self.writes.append(message)

    async def close(self) -> None:
        self.closed = True

    async def emit_video(self, chunk: EncodedChunk) -> None:
        if self.on_video is not None:
            result = self.on_video(chunk)
            if asyncio.iscoroutine(result):
                await result

    def emit_fatal(self, message: str = "capture stopped") -> None:
        assert self.on_fatal is not None
        self.on_fatal(ScrcpyProtocolError(message))


class _TransportFactory:
    def __init__(self, start_results: list[bool] | None = None):
        self.start_results = list(start_results or [])
        self.instances: list[_Transport] = []

    def __call__(self, *args: object, **kwargs: object) -> _Transport:
        fail_start = self.start_results.pop(0) if self.start_results else False
        transport = _Transport(*args, fail_start=fail_start, **kwargs)
        self.instances.append(transport)
        return transport


class BoundedControlQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_drops_old_output_instead_of_blocking_producer(self) -> None:
        queue = BoundedControlQueue(max_messages=2, max_bytes=8)

        self.assertTrue(queue.push(b"1111"))
        self.assertTrue(queue.push(b"2222"))
        self.assertTrue(queue.push(b"3333"))

        self.assertEqual(queue.dropped_messages, 1)
        self.assertEqual(await queue.receive(), b"2222")
        self.assertEqual(await queue.receive(), b"3333")
        queue.close()
        with self.assertRaises(ControlStreamClosed):
            await queue.receive()


class DirectSessionTests(unittest.IsolatedAsyncioTestCase):
    def make_session(
        self,
        factory: _TransportFactory | None = None,
        *,
        mode: QualityMode = QualityMode.AUTO,
    ) -> tuple[DirectSession, _TransportFactory]:
        factory = factory or _TransportFactory()
        device = DeviceRecord(
            "dev_test",
            "emulator-5562",
            "Android 15",
            "MuMu",
            "15",
            "device",
            True,
            False,
            1,
        )
        database = _Database()
        session = DirectSession(
            "ses_test",
            "client",
            device,
            database,  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            Path("scrcpy-server"),
            Path("ime.apk"),
            initial_quality_mode=mode,
            transport_factory=factory,  # type: ignore[arg-type]
            ime_factory=lambda *_args: _Ime(),  # type: ignore[arg-type]
        )
        return session, factory

    async def asyncTearDown(self) -> None:
        tasks = [
            task
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and task.get_name().startswith(("ime-start-", "media-recovery-", "remote-"))
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_uses_upstream_video_only_transport(self) -> None:
        session, factory = self.make_session(mode=QualityMode.ORIGINAL)
        await session.start()
        try:
            transport = factory.instances[0]
            self.assertFalse(transport.kwargs["audio_enabled"])
            self.assertNotIn("device_agent_key_path", transport.kwargs)
            self.assertEqual(transport.kwargs["max_size"], 4096)
            self.assertEqual(transport.kwargs["max_fps"], 60)
        finally:
            await session.close()

    async def test_control_and_video_subscriptions_are_independent(self) -> None:
        session, factory = self.make_session()
        await session.start()
        control = await session.attach_control()
        video = await session.attach_video()
        try:
            self.assertEqual(len(factory.instances), 2)
            self.assertTrue(factory.instances[0].closed)
            self.assertEqual((await video.incoming.receive()).kind, PACKET_RESET)
            await factory.instances[-1].emit_video(EncodedChunk(b"idr", 10, True))
            self.assertEqual((await video.incoming.receive()).kind, PACKET_VIDEO)

            first_output = await control.outgoing.receive()
            second_output = await control.outgoing.receive()
            self.assertTrue(first_output)
            self.assertTrue(second_output)

            replacement = await session.attach_control()
            self.assertIs(session._video_connection, video)
            with self.assertRaises(ControlStreamClosed):
                await control.outgoing.receive()
            await session.detach(control)
            self.assertIs(session._control_connection, replacement)
            await session.detach(replacement)
        finally:
            await session.detach(video)
            await session.close()

    async def test_1080p_change_restarts_capture_without_replacing_sockets(self) -> None:
        session, factory = self.make_session()
        await session.start()
        control = await session.attach_control()
        video = await session.attach_video()
        await video.incoming.receive()
        try:
            await session._handle_quality_request(QualityMode.P1080, 7)

            self.assertEqual(len(factory.instances), 3)
            self.assertTrue(factory.instances[1].closed)
            self.assertEqual(factory.instances[2].kwargs["max_size"], 1920)
            self.assertEqual(factory.instances[2].kwargs["bitrate_bps"], 7_000_000)
            self.assertFalse(factory.instances[2].kwargs["audio_enabled"])
            self.assertIs(session._control_connection, control)
            self.assertIs(session._video_connection, video)
            self.assertEqual((await video.incoming.receive()).kind, PACKET_RESET)
        finally:
            await session.close()

    async def test_manual_quality_freeze_restarts_media_with_cooldown(self) -> None:
        session, factory = self.make_session(mode=QualityMode.ORIGINAL)
        session.RECEIVER_FREEZE_RESTART_COOLDOWN_SECONDS = 999.0
        await session.start()
        control = await session.attach_control()
        video = await session.attach_video()
        try:
            baseline = len(factory.instances)
            sample = ReceiverSample(0.0, 0.0, 0.0, 1, 0)
            await session._handle_receiver_stats(sample)

            self.assertEqual(len(factory.instances), baseline + 1)
            self.assertEqual(session.quality.mode, QualityMode.ORIGINAL)
            self.assertEqual(factory.instances[-1].kwargs["max_size"], 4096)
            self.assertIs(session._control_connection, control)
            self.assertIs(session._video_connection, video)

            await session._handle_receiver_stats(
                ReceiverSample(0.0, 0.0, 0.0, 2, 0)
            )
            self.assertEqual(len(factory.instances), baseline + 1)

            session.RECEIVER_FREEZE_RESTART_COOLDOWN_SECONDS = 0.0
            await session._handle_receiver_stats(
                ReceiverSample(0.0, 0.0, 0.0, 3, 0)
            )
            self.assertEqual(len(factory.instances), baseline + 2)
        finally:
            await session.close()

    async def test_video_reconnect_does_not_disconnect_control_or_end_session(self) -> None:
        session, _ = self.make_session()
        await session.start()
        control = await session.attach_control()
        video = await session.attach_video()
        try:
            await session.detach(video)

            self.assertIs(session._control_connection, control)
            self.assertEqual(session.state, "connected")
            replacement = await session.attach_video()
            self.assertIs(session._control_connection, control)
            self.assertEqual(session.state, "connected")
            await session.detach(replacement)
        finally:
            await session.close()

    async def test_media_failure_retries_without_closing_control_connection(self) -> None:
        factory = _TransportFactory([False, True, False])
        session, _ = self.make_session(factory)
        session.MEDIA_RETRY_INITIAL_SECONDS = 0.0
        session.MEDIA_RETRY_MAX_SECONDS = 0.0
        await session.start()
        control = await session.attach_control()
        first = factory.instances[0]
        try:
            first.emit_fatal()
            for _ in range(100):
                if len(factory.instances) == 3 and session._media_recovery_task is None:
                    break
                await asyncio.sleep(0)

            self.assertEqual(len(factory.instances), 3)
            self.assertTrue(first.closed)
            self.assertTrue(factory.instances[1].closed)
            self.assertIs(session._control_connection, control)
            self.assertEqual(session.state, "connected")
            self.assertIs(session.transport, factory.instances[2])
        finally:
            await session.close()

    async def test_video_queue_overload_restarts_only_media_for_a_new_idr(self) -> None:
        session, factory = self.make_session()
        session.MEDIA_RETRY_INITIAL_SECONDS = 0.0
        session.MEDIA_RETRY_MAX_SECONDS = 0.0
        await session.start()
        control = await session.attach_control()
        video = await session.attach_video()
        await video.incoming.receive()
        first = factory.instances[-1]
        try:
            for sequence in range(7):
                await first.emit_video(
                    EncodedChunk(bytes([sequence]), sequence * 10_000, sequence == 0)
                )
            for _ in range(100):
                if len(factory.instances) == 3 and session._media_recovery_task is None:
                    break
                await asyncio.sleep(0)

            self.assertEqual(session.video.overload_count, 1)
            self.assertEqual(len(factory.instances), 3)
            self.assertTrue(first.closed)
            self.assertIs(session._control_connection, control)
            self.assertIs(session._video_connection, video)
            self.assertEqual(session.state, "connected")
            self.assertEqual((await video.incoming.receive()).kind, PACKET_RESET)
            session.record_video_sent(PACKET_VIDEO, 123)
            metrics = session.stream_metrics
            self.assertEqual(metrics["framesCaptured"], 7)
            self.assertEqual(metrics["framesPublished"], 6)
            self.assertGreaterEqual(metrics["queueDroppedFrames"], 7)
            self.assertEqual(metrics["queueOverloads"], 1)
            self.assertEqual(metrics["videoWsFramesSent"], 1)
            self.assertEqual(metrics["videoWsBytesSent"], 123)
            self.assertGreaterEqual(metrics["mediaRestarts"], 2)
        finally:
            await session.close()

    async def test_invalid_control_frame_is_nonfatal(self) -> None:
        session, _ = self.make_session()
        await session.start()
        control = await session.attach_control()
        try:
            self.assertFalse(await session.receive_control(b"not protobuf", control.epoch))
            self.assertIs(session._control_connection, control)
            self.assertEqual(session.state, "connected")
        finally:
            await session.close()


if __name__ == "__main__":
    unittest.main()
