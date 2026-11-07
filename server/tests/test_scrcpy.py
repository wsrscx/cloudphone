from __future__ import annotations

import asyncio
import struct
import tempfile
import unittest
from pathlib import Path

from cloudphone.scrcpy import (
    PACKET_FLAG_CONFIG,
    PACKET_FLAG_KEY_FRAME,
    PACKET_FLAG_SESSION,
    ContinuousTimestamp,
    EncodedChunk,
    H264ConfigCache,
    ScrcpyControlEncoder,
    ScrcpyFrameParser,
    ScrcpyProtocolError,
    ScrcpyTransport,
    StreamGeometry,
)


class ScrcpyProtocolTests(unittest.TestCase):
    def test_fragmented_frame_metadata(self) -> None:
        framed = struct.pack(">QI", PACKET_FLAG_KEY_FRAME | 1234, 4) + b"data"
        parser = ScrcpyFrameParser()
        self.assertEqual(parser.feed(framed[:7]), [])
        chunks = parser.feed(framed[7:])
        self.assertEqual(chunks, [EncodedChunk(b"data", 1234, True, False)])

    def test_rejects_unbounded_packet(self) -> None:
        parser = ScrcpyFrameParser(max_packet_size=10)
        with self.assertRaises(ScrcpyProtocolError):
            parser.feed(struct.pack(">QI", 0, 11))

    def test_codec_config_is_prefixed_to_first_idr(self) -> None:
        cache = H264ConfigCache()
        config = b"\x00\x00\x00\x01\x67abc\x00\x00\x00\x01\x68de"
        self.assertIsNone(cache.process(EncodedChunk(config, None, False, True)))
        idr = EncodedChunk(b"\x00\x00\x00\x01\x65frame", 7, True)
        result = cache.process(idr)
        self.assertEqual(result.data, config + idr.data)  # type: ignore[union-attr]
        self.assertEqual(cache.generation, 1)

    def test_split_sps_and_pps_config_are_both_prefixed(self) -> None:
        cache = H264ConfigCache()
        sps = b"\x00\x00\x00\x01\x67sps"
        pps = b"\x00\x00\x01\x68pps"
        self.assertIsNone(cache.process(EncodedChunk(sps, None, False, True)))
        self.assertIsNone(cache.process(EncodedChunk(pps, None, False, True)))

        idr = EncodedChunk(b"\x00\x00\x00\x01\x65frame", 7, True)
        result = cache.process(idr)

        self.assertEqual(result.data, sps + pps + idr.data)  # type: ignore[union-attr]

    def test_continuous_timestamp_survives_source_reset(self) -> None:
        clock = ContinuousTimestamp(16_667)
        self.assertEqual(clock.map(900_000), 0)
        self.assertEqual(clock.map(916_667), 16_667)
        self.assertEqual(clock.map(0), 16_668)

    def test_control_message_shapes(self) -> None:
        touch = ScrcpyControlEncoder.touch(0, 9, 100, 200, 1920, 1080, 65535)
        self.assertEqual(len(touch), 32)
        self.assertEqual(touch[0], ScrcpyControlEncoder.TYPE_INJECT_TOUCH)
        self.assertEqual(len(ScrcpyControlEncoder.navigation(4)), 28)
        self.assertEqual(ScrcpyControlEncoder.rotate(), b"\x0b")

    def test_config_flag_has_no_pts(self) -> None:
        parser = ScrcpyFrameParser()
        chunk = parser.feed(struct.pack(">QI", PACKET_FLAG_CONFIG, 1) + b"x")[0]
        self.assertTrue(chunk.config)
        self.assertIsNone(chunk.pts_us)

    def test_session_metadata_reports_geometry(self) -> None:
        parser = ScrcpyFrameParser()
        metadata = struct.pack(">III", (PACKET_FLAG_SESSION >> 32) | 1, 1280, 720)
        self.assertEqual(parser.feed(metadata[:7]), [])
        self.assertEqual(parser.feed(metadata[7:]), [StreamGeometry(1280, 720, True)])

    def test_scid_is_always_a_non_negative_java_int(self) -> None:
        transport = ScrcpyTransport(None, "serial", None)  # type: ignore[arg-type]
        self.assertLessEqual(int(transport.scid, 16), 0x7FFFFFFF)
        self.assertEqual(len(transport.scid), 8)

class _OneShotReader:
    def __init__(self, payload: bytes):
        self.payload = payload

    async def read(self, _size: int) -> bytes:
        payload, self.payload = self.payload, b""
        return payload


class ScrcpyStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_burst_yields_after_bounded_dispatch_batch(self) -> None:
        first = struct.pack(">QI", PACKET_FLAG_KEY_FRAME | 10_000, 1) + b"a"
        second = struct.pack(">QI", 20_000, 1) + b"b"
        third = struct.pack(">QI", 30_000, 1) + b"c"
        events: list[str] = []
        transport = object.__new__(ScrcpyTransport)

        def on_video(chunk: EncodedChunk) -> None:
            events.append(chunk.data.decode("ascii"))
            if chunk.data == b"a":
                asyncio.get_running_loop().call_soon(events.append, "ws-drain")

        transport.on_video = on_video
        with self.assertRaises(ScrcpyProtocolError):
            await transport._read_stream(_OneShotReader(first + second + third), True)

        self.assertEqual(events, ["a", "ws-drain", "b", "c"])

    async def test_direct_start_obeys_disabled_audio_setting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            server_jar = Path(temporary) / "server.jar"
            server_jar.write_bytes(b"jar")
            class PushAdb:
                def __init__(self) -> None:
                    self.remote_path = ""

                async def push_if_changed(
                    self, _serial: str, _local: Path, remote: str
                ) -> None:
                    self.remote_path = remote

            adb = PushAdb()
            transport = ScrcpyTransport(
                adb, "serial", server_jar, audio_enabled=False
            )  # type: ignore[arg-type]
            attempts: list[bool] = []

            async def start_once(*, audio_enabled: bool) -> None:
                attempts.append(audio_enabled)

            transport._start_once = start_once  # type: ignore[method-assign]
            await transport.start()

        self.assertEqual(attempts, [False])
        self.assertFalse(transport.audio_available)
        self.assertEqual(adb.remote_path, ScrcpyTransport.REMOTE_JAR)

    async def test_direct_start_does_not_retry_with_different_audio_setting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            server_jar = Path(temporary) / "server.jar"
            server_jar.write_bytes(b"server")

            class PushAdb:
                async def push_if_changed(self, *_args: object) -> None:
                    return None

            transport = ScrcpyTransport(
                PushAdb(), "serial", server_jar, audio_enabled=True
            )  # type: ignore[arg-type]
            attempts: list[bool] = []

            async def start_once(*, audio_enabled: bool) -> None:
                attempts.append(audio_enabled)
                raise ScrcpyProtocolError("opus initialization failed")

            transport._start_once = start_once  # type: ignore[method-assign]
            with self.assertRaisesRegex(ScrcpyProtocolError, "opus"):
                await transport.start()

        self.assertEqual(attempts, [True])

    async def test_audio_codec_config_is_not_forwarded(self) -> None:
        config = struct.pack(">QI", PACKET_FLAG_CONFIG, 4) + b"head"
        audio = struct.pack(">QI", 10_000, 5) + b"audio"
        received: list[EncodedChunk] = []
        transport = object.__new__(ScrcpyTransport)
        transport.on_audio = lambda chunk: received.append(chunk)

        with self.assertRaises(ScrcpyProtocolError):
            await transport._read_stream(_OneShotReader(config + audio), False)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].data, b"audio")
        self.assertFalse(received[0].config)

    async def test_core_task_failure_reports_fatal_error(self) -> None:
        transport = ScrcpyTransport(None, "serial", None)  # type: ignore[arg-type]
        failures: list[ScrcpyProtocolError] = []
        transport.on_fatal = failures.append

        async def fail() -> None:
            raise RuntimeError("video reader stopped")

        task = asyncio.create_task(fail())
        transport._core_tasks.add(task)
        task.add_done_callback(transport._task_finished)
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ScrcpyProtocolError)
        self.assertIn("video reader stopped", str(failures[0]))
