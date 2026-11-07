from __future__ import annotations

import asyncio
import unittest

from cloudphone.direct_stream import (
    PACKET_RESET,
    PACKET_VIDEO,
    DirectStreamClosed,
    DirectVideoHub,
    LatestDecodableQueue,
    VideoPacket,
    decode_packet,
)
from cloudphone.scrcpy import EncodedChunk


def _packet(sequence: int, *, keyframe: bool = False, size: int = 10, pts_us: int | None = None) -> VideoPacket:
    return VideoPacket(PACKET_VIDEO, 1, sequence, sequence * 33_333 if pts_us is None else pts_us,
                       1920, 1080, keyframe, bytes([sequence & 0xFF]) * size)


class DirectStreamTests(unittest.IsolatedAsyncioTestCase):
    def test_binary_packet_round_trip(self) -> None:
        original = _packet(7, keyframe=True, size=1024)
        self.assertEqual(decode_packet(original.encode()), original)
        with self.assertRaises(ValueError):
            decode_packet(original.encode()[:-1])

    async def test_subscription_starts_with_reset_and_waits_for_keyframe(self) -> None:
        hub = DirectVideoHub()
        hub.geometry(1920, 1080)
        subscriber = hub.attach()
        reset = await subscriber.receive()
        self.assertEqual((reset.kind, reset.width, reset.height), (PACKET_RESET, 1920, 1080))
        self.assertFalse(hub.publish(EncodedChunk(b"delta", 1, False)))
        self.assertTrue(hub.publish(EncodedChunk(b"idr", 2, True)))
        self.assertTrue((await subscriber.receive()).keyframe)

    async def test_overload_drops_partial_gop_and_requires_new_keyframe(self) -> None:
        queue = LatestDecodableQueue(max_frames=2, max_bytes=1000, max_span_us=1_000_000)
        queue.push(_packet(1, keyframe=True))
        queue.push(_packet(2))
        self.assertFalse(queue.push(_packet(3)))
        self.assertTrue(queue.waiting_for_keyframe)
        self.assertFalse(queue.push(_packet(4)))
        self.assertTrue(queue.push(_packet(5, keyframe=True)))
        self.assertEqual((await queue.receive()).sequence, 5)
        self.assertGreaterEqual(queue.dropped_frames, 4)

    async def test_geometry_change_discards_old_generation(self) -> None:
        hub = DirectVideoHub()
        subscriber = hub.attach()
        await subscriber.receive()
        hub.publish(EncodedChunk(b"old", 1, True))
        hub.geometry(1280, 720)
        reset = await subscriber.receive()
        self.assertEqual(reset.kind, PACKET_RESET)
        self.assertEqual((reset.width, reset.height), (1280, 720))
        self.assertFalse(hub.publish(EncodedChunk(b"delta", 2, False)))

    async def test_new_subscriber_supersedes_old_one(self) -> None:
        hub = DirectVideoHub()
        old = hub.attach()
        replacement = hub.attach()
        with self.assertRaises(DirectStreamClosed):
            await asyncio.wait_for(old.receive(), 0.1)
        self.assertEqual((await replacement.receive()).kind, PACKET_RESET)

    async def test_overload_event_is_explicit_and_requires_a_subscriber(self) -> None:
        hub = DirectVideoHub()
        overloads: list[bool] = []
        hub.on_overload = lambda: overloads.append(True)

        self.assertFalse(hub.publish(EncodedChunk(b"unwatched", 1, True)))
        self.assertEqual(overloads, [])

        subscriber = hub.attach()
        await subscriber.receive()
        for sequence in range(7):
            hub.publish(EncodedChunk(bytes([sequence]), sequence * 10_000, sequence == 0))

        self.assertEqual(overloads, [True])
        self.assertEqual(hub.overload_count, 1)
        self.assertTrue(subscriber.waiting_for_keyframe)


if __name__ == "__main__":
    unittest.main()
