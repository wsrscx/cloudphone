from __future__ import annotations

import asyncio
import struct
from collections import deque
from dataclasses import dataclass
from typing import Callable

from .scrcpy import EncodedChunk


VIDEO_MAGIC = b"CPV2"
VIDEO_VERSION = 2
PACKET_VIDEO = 1
PACKET_RESET = 2
FLAG_KEY_FRAME = 1
VIDEO_HEADER = struct.Struct(">4sBBHIQQHHI")
MAX_VIDEO_PAYLOAD = 16 * 1024 * 1024


class DirectStreamClosed(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class VideoPacket:
    kind: int
    generation: int
    sequence: int
    pts_us: int
    width: int
    height: int
    keyframe: bool
    payload: bytes

    def encode(self) -> bytes:
        if self.kind not in {PACKET_VIDEO, PACKET_RESET}:
            raise ValueError("invalid direct-stream packet type")
        if len(self.payload) > MAX_VIDEO_PAYLOAD:
            raise ValueError("direct-stream video packet is too large")
        flags = FLAG_KEY_FRAME if self.keyframe else 0
        return VIDEO_HEADER.pack(
            VIDEO_MAGIC,
            VIDEO_VERSION,
            self.kind,
            flags,
            self.generation,
            self.sequence,
            self.pts_us,
            self.width,
            self.height,
            len(self.payload),
        ) + self.payload


def decode_packet(data: bytes) -> VideoPacket:
    if len(data) < VIDEO_HEADER.size:
        raise ValueError("truncated direct-stream packet")
    magic, version, kind, flags, generation, sequence, pts_us, width, height, size = VIDEO_HEADER.unpack_from(data)
    if magic != VIDEO_MAGIC or version != VIDEO_VERSION:
        raise ValueError("unsupported direct-stream packet")
    if kind not in {PACKET_VIDEO, PACKET_RESET} or size > MAX_VIDEO_PAYLOAD:
        raise ValueError("invalid direct-stream packet header")
    if len(data) != VIDEO_HEADER.size + size:
        raise ValueError("direct-stream packet size mismatch")
    return VideoPacket(
        kind,
        generation,
        sequence,
        pts_us,
        width,
        height,
        bool(flags & FLAG_KEY_FRAME),
        data[VIDEO_HEADER.size :],
    )


class LatestDecodableQueue:
    """A bounded queue that never emits a partial H.264 GOP after overload."""

    def __init__(self, *, max_frames: int = 6, max_bytes: int = 4 * 1024 * 1024, max_span_us: int = 150_000):
        if max_frames < 1 or max_bytes < 1 or max_span_us < 1:
            raise ValueError("direct-stream queue limits must be positive")
        self.max_frames = max_frames
        self.max_bytes = max_bytes
        self.max_span_us = max_span_us
        self._packets: deque[VideoPacket] = deque()
        self._bytes = 0
        self._ready = asyncio.Event()
        self._closed = False
        self._waiting_for_keyframe = True
        self.dropped_frames = 0
        self.overload_count = 0

    @property
    def waiting_for_keyframe(self) -> bool:
        return self._waiting_for_keyframe

    def reset(self, packet: VideoPacket) -> None:
        if packet.kind != PACKET_RESET:
            raise ValueError("queue reset requires a reset packet")
        self._drop_all()
        self._waiting_for_keyframe = True
        if not self._closed:
            self._append(packet)

    def push(self, packet: VideoPacket) -> bool:
        if self._closed:
            return False
        if packet.kind == PACKET_RESET:
            self.reset(packet)
            return True
        if packet.kind != PACKET_VIDEO:
            raise ValueError("queue accepts only video or reset packets")
        if self._waiting_for_keyframe:
            if not packet.keyframe:
                self.dropped_frames += 1
                return False
            self._waiting_for_keyframe = False
        self._append(packet)
        if self._over_limit():
            self.overload_count += 1
            dropped = len([queued for queued in self._packets if queued.kind == PACKET_VIDEO])
            self._drop_all()
            self.dropped_frames += dropped
            self._waiting_for_keyframe = True
            # A key frame that itself exceeded a byte limit is not useful to
            # retain; the next key frame starts a clean decoder epoch.
            return False
        return True

    async def receive(self) -> VideoPacket:
        while True:
            if self._packets:
                packet = self._packets.popleft()
                self._bytes -= len(packet.payload)
                if not self._packets:
                    self._ready.clear()
                return packet
            if self._closed:
                raise DirectStreamClosed
            await self._ready.wait()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._drop_all()
        self._ready.set()

    def _append(self, packet: VideoPacket) -> None:
        self._packets.append(packet)
        self._bytes += len(packet.payload)
        self._ready.set()

    def _over_limit(self) -> bool:
        video = [packet for packet in self._packets if packet.kind == PACKET_VIDEO]
        if len(video) > self.max_frames or self._bytes > self.max_bytes:
            return True
        return len(video) > 1 and video[-1].pts_us - video[0].pts_us > self.max_span_us

    def _drop_all(self) -> None:
        self._packets.clear()
        self._bytes = 0
        self._ready.clear()


class DirectVideoHub:
    """Single-viewer H.264 publisher with replaceable subscriptions."""

    def __init__(self) -> None:
        self.width = 1
        self.height = 1
        self.generation = 1
        self.sequence = 0
        self.frames_captured = 0
        self.bytes_captured = 0
        self.frames_published = 0
        self.bytes_published = 0
        self.overload_count = 0
        self.on_overload: Callable[[], None] | None = None
        self._subscriber: LatestDecodableQueue | None = None
        self._retired_dropped_frames = 0

    def attach(self) -> LatestDecodableQueue:
        if self._subscriber is not None:
            self._retire(self._subscriber)
        subscriber = LatestDecodableQueue()
        self._subscriber = subscriber
        subscriber.reset(self._packet(PACKET_RESET, b"", 0, False))
        return subscriber

    def detach(self, subscriber: LatestDecodableQueue) -> None:
        if self._subscriber is subscriber:
            self._retire(subscriber)
            self._subscriber = None
        else:
            subscriber.close()

    @property
    def queue_dropped_frames(self) -> int:
        active = self._subscriber.dropped_frames if self._subscriber is not None else 0
        return self._retired_dropped_frames + active

    def geometry(self, width: int, height: int, *, force_generation: bool = False) -> None:
        if not (1 <= width <= 32768 and 1 <= height <= 32768):
            raise ValueError("invalid direct-stream geometry")
        changed = (width, height) != (self.width, self.height)
        self.width, self.height = width, height
        if changed or force_generation:
            self.generation += 1
            if self._subscriber is not None:
                self._subscriber.reset(self._packet(PACKET_RESET, b"", 0, False))

    def publish(self, chunk: EncodedChunk) -> bool:
        if chunk.config:
            raise ValueError("codec configuration must be joined to the following key frame")
        self.frames_captured += 1
        self.bytes_captured += len(chunk.data)
        packet = self._packet(PACKET_VIDEO, chunk.data, chunk.pts_us or 0, chunk.keyframe)
        subscriber = self._subscriber
        if subscriber is None:
            return False
        previous_overloads = subscriber.overload_count
        accepted = subscriber.push(packet)
        if subscriber.overload_count > previous_overloads:
            self.overload_count += subscriber.overload_count - previous_overloads
            if self.on_overload is not None:
                self.on_overload()
        if accepted:
            self.frames_published += 1
            self.bytes_published += len(chunk.data)
        return accepted

    def close(self) -> None:
        if self._subscriber is not None:
            self._retire(self._subscriber)
            self._subscriber = None

    def _retire(self, subscriber: LatestDecodableQueue) -> None:
        self._retired_dropped_frames += subscriber.dropped_frames
        subscriber.close()

    def _packet(self, kind: int, payload: bytes, pts_us: int, keyframe: bool) -> VideoPacket:
        self.sequence += 1
        return VideoPacket(
            kind,
            self.generation,
            self.sequence,
            max(0, pts_us),
            self.width,
            self.height,
            keyframe,
            payload,
        )
