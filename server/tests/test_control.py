from __future__ import annotations

import asyncio
import struct
import unittest

from cloudphone.control import ControlOverloadError, RemoteControlRouter
from cloudphone.wire import ProtocolError, encode_varint


def _key(number: int, wire_type: int) -> bytes:
    return encode_varint((number << 3) | wire_type)


def _uint(number: int, value: int) -> bytes:
    return _key(number, 0) + encode_varint(value)


def _blob(number: int, value: bytes) -> bytes:
    return _key(number, 2) + encode_varint(len(value)) + value


def _envelope(sequence: int, payload_field: int, payload: bytes = b"") -> bytes:
    return _uint(1, 1) + _uint(2, sequence) + _blob(payload_field, payload)


def _move(sequence: int, x: int) -> bytes:
    point = _uint(1, 1) + _uint(2, x) + _uint(3, 2) + _uint(4, 65535)
    return _envelope(sequence, 11, _blob(1, point))


def _touch(sequence: int) -> bytes:
    point = _uint(1, 1) + _uint(2, 1) + _uint(3, 2) + _uint(4, 65535)
    return _envelope(sequence, 10, _uint(1, 1) + _blob(2, point))


def _quality(sequence: int) -> bytes:
    return _envelope(sequence, 14, _uint(1, sequence) + _uint(2, 1))


class RemoteControlRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_motion_slot_replaces_backlog_with_latest_batch(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        received: list[int] = []

        async def handle(envelope) -> None:  # type: ignore[no-untyped-def]
            received.append(int(envelope.payload["points"][0]["x"]))
            if len(received) == 1:
                entered.set()
                await release.wait()

        router = RemoteControlRouter(handle, self.fail)
        router.start()
        try:
            router.submit(_move(1, 1), "motion.v2")
            await entered.wait()
            for sequence in range(2, 1002):
                router.submit(_move(sequence, sequence), "motion.v2")
            release.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertEqual(received, [1, 1001])
        finally:
            await router.close()

    async def test_slow_quality_change_cannot_block_input_lane(self) -> None:
        quality_entered = asyncio.Event()
        release_quality = asyncio.Event()
        input_received = asyncio.Event()

        async def handle(envelope) -> None:  # type: ignore[no-untyped-def]
            if envelope.payload_type == "quality_request":
                quality_entered.set()
                await release_quality.wait()
            elif envelope.payload_type == "touch":
                input_received.set()

        router = RemoteControlRouter(handle, self.fail)
        router.start()
        try:
            router.submit(_quality(1), "control.v2")
            await quality_entered.wait()
            router.submit(_touch(2), "input.v2")
            await asyncio.wait_for(input_received.wait(), 0.2)
        finally:
            release_quality.set()
            await router.close()

    async def test_sequence_filter_is_independent_per_channel(self) -> None:
        received: list[int] = []

        async def handle(envelope) -> None:  # type: ignore[no-untyped-def]
            received.append(envelope.sequence)

        router = RemoteControlRouter(handle, self.fail)
        router.start()
        try:
            self.assertTrue(router.submit(_touch(10), "input.v2"))
            self.assertFalse(router.submit(_touch(9), "input.v2"))
            self.assertTrue(router.submit(_move(9, 9), "motion.v2"))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertCountEqual(received, [10, 9])
        finally:
            await router.close()

    async def test_new_peer_generation_cancels_old_work_and_resets_sequences(self) -> None:
        old_entered = asyncio.Event()
        fresh_received = asyncio.Event()
        received: list[int] = []

        async def handle(envelope) -> None:  # type: ignore[no-untyped-def]
            if envelope.sequence == 100:
                old_entered.set()
                await asyncio.Event().wait()
            received.append(envelope.sequence)
            fresh_received.set()

        router = RemoteControlRouter(handle, self.fail)
        router.start()
        try:
            router.submit(_touch(100), "input.v2")
            await old_entered.wait()
            router.submit(_touch(101), "input.v2")

            await router.reset()
            self.assertTrue(router.submit(_touch(1), "input.v2"))
            await asyncio.wait_for(fresh_received.wait(), 0.2)

            self.assertEqual(received, [1])
        finally:
            await router.close()

    async def test_rejects_cross_lane_payload_and_bounded_reliable_queue(self) -> None:
        blocker = asyncio.Event()

        async def handle(_envelope) -> None:  # type: ignore[no-untyped-def]
            await blocker.wait()

        router = RemoteControlRouter(handle, self.fail, max_input_commands=1)
        router.start()
        try:
            with self.assertRaises(ProtocolError):
                router.submit(_move(1, 1), "input.v2")
            router.submit(_touch(2), "input.v2")
            with self.assertRaises(ControlOverloadError):
                router.submit(_touch(3), "input.v2")
        finally:
            blocker.set()
            await router.close()


if __name__ == "__main__":
    unittest.main()
