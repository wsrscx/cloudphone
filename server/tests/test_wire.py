from __future__ import annotations

import struct
import unittest

from cloudphone.wire import ProtocolError, decode_envelope, encode_server_envelope, encode_varint


def key(number: int, wire_type: int) -> bytes:
    return encode_varint((number << 3) | wire_type)


def uint(number: int, value: int) -> bytes:
    return key(number, 0) + encode_varint(value)


def blob(number: int, value: bytes) -> bytes:
    return key(number, 2) + encode_varint(len(value)) + value


def envelope(payload_field: int, payload: bytes, *, generation: int = 0) -> bytes:
    return uint(1, 1) + uint(2, 9) + uint(3, generation) + blob(payload_field, payload)


class WireTests(unittest.TestCase):
    def test_decodes_appended_original_quality_mode(self) -> None:
        decoded = decode_envelope(envelope(14, uint(1, 22) + uint(2, 5)))
        self.assertEqual(decoded.payload, {"requestId": 22, "mode": 5})

    def test_decodes_ten_pointer_move_batch(self) -> None:
        points = b""
        for pointer_id in range(10):
            point = uint(1, pointer_id) + uint(2, pointer_id * 100) + uint(3, 65535) + uint(4, 32768)
            points += blob(1, point)
        decoded = decode_envelope(envelope(11, points, generation=4))
        self.assertEqual(decoded.payload_type, "touch_moves")
        self.assertEqual(decoded.video_generation, 4)
        self.assertEqual(len(decoded.payload["points"]), 10)

    def test_rejects_more_than_ten_pointers(self) -> None:
        points = b"".join(blob(1, uint(1, value)) for value in range(11))
        with self.assertRaises(ProtocolError):
            decode_envelope(envelope(11, points))

    def test_decodes_stats_fixed64_fields(self) -> None:
        stats = (
            key(1, 1) + struct.pack("<d", 51.5)
            + key(2, 1) + struct.pack("<d", 0.04)
            + key(3, 1) + struct.pack("<d", 12.0)
            + uint(4, 3) + uint(5, 99) + key(6, 1) + struct.pack("<d", 59.2) + uint(7, 8_000_000)
            + key(8, 1) + struct.pack("<d", 2.5) + uint(9, 4) + uint(10, 7_200_000)
        )
        decoded = decode_envelope(envelope(15, stats))
        self.assertAlmostEqual(decoded.payload["rttMs"], 51.5)
        self.assertAlmostEqual(decoded.payload["playoutBufferMs"], 12.0)
        self.assertAlmostEqual(decoded.payload["jitterMs"], 2.5)
        self.assertEqual(decoded.payload["framesDropped"], 4)
        self.assertEqual(decoded.payload["availableBitrateBps"], 8_000_000)
        self.assertEqual(decoded.payload["incomingBitrateBps"], 7_200_000)

    def test_ime_utf8_boundary_and_invalid_utf8(self) -> None:
        text = "界" * 5461
        decoded = decode_envelope(envelope(16, blob(1, text.encode()) + uint(2, 6)))
        self.assertEqual(decoded.payload["text"], text)
        with self.assertRaises(ProtocolError):
            decode_envelope(envelope(16, blob(1, b"\xff")))

    def test_rejects_wrong_version_and_multiple_payloads(self) -> None:
        with self.assertRaises(ProtocolError):
            decode_envelope(uint(1, 2) + blob(13, b""))
        with self.assertRaises(ProtocolError):
            decode_envelope(uint(1, 1) + blob(13, b"") + blob(17, b""))

    def test_encodes_server_quality_applied(self) -> None:
        encoded = encode_server_envelope(
            7, 2, "quality_applied",
            {"requestId": 8, "mode": 3, "width": 1280, "height": 720, "fps": 60, "bitrateBps": 6_000_000, "encoderRestarted": True},
        )
        self.assertIn(blob(31, b"" )[:1], encoded)
        self.assertLess(len(encoded), 128)

    def test_varint_overflow_is_rejected(self) -> None:
        with self.assertRaises(ProtocolError):
            decode_envelope(b"\x80" * 11)
