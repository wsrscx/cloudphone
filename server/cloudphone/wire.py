from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Any, Iterator


PROTOCOL_VERSION = 1
MAX_ENVELOPE_BYTES = 65_536
MAX_IME_TEXT_BYTES = 16_384


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EnvelopeMessage:
    sequence: int
    video_generation: int
    payload_type: str
    payload: dict[str, Any]


def encode_varint(value: int) -> bytes:
    if value < 0 or value > 0xFFFFFFFFFFFFFFFF:
        raise ProtocolError("varint is outside uint64 range")
    output = bytearray()
    while value > 0x7F:
        output.append((value & 0x7F) | 0x80)
        value >>= 7
    output.append(value)
    return bytes(output)


def _decode_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise ProtocolError("truncated varint")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            if shift == 63 and byte > 1:
                raise ProtocolError("varint overflow")
            return value, offset
    raise ProtocolError("varint overflow")


def _fields(data: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    offset = 0
    while offset < len(data):
        key, offset = _decode_varint(data, offset)
        number, wire_type = key >> 3, key & 7
        if number == 0:
            raise ProtocolError("field number zero is invalid")
        if wire_type == 0:
            value, offset = _decode_varint(data, offset)
            yield number, wire_type, value
        elif wire_type == 1:
            if offset + 8 > len(data):
                raise ProtocolError("truncated fixed64")
            yield number, wire_type, data[offset : offset + 8]
            offset += 8
        elif wire_type == 2:
            size, offset = _decode_varint(data, offset)
            if size > MAX_ENVELOPE_BYTES or offset + size > len(data):
                raise ProtocolError("invalid length-delimited field")
            yield number, wire_type, data[offset : offset + size]
            offset += size
        elif wire_type == 5:
            if offset + 4 > len(data):
                raise ProtocolError("truncated fixed32")
            yield number, wire_type, data[offset : offset + 4]
            offset += 4
        else:
            raise ProtocolError(f"unsupported protobuf wire type {wire_type}")


def _message_map(data: bytes) -> dict[int, list[tuple[int, int | bytes]]]:
    output: dict[int, list[tuple[int, int | bytes]]] = {}
    for number, wire_type, value in _fields(data):
        output.setdefault(number, []).append((wire_type, value))
    return output


def _varint(mapping: dict[int, list[tuple[int, int | bytes]]], field: int, default: int = 0) -> int:
    values = mapping.get(field)
    if not values:
        return default
    wire_type, value = values[-1]
    if wire_type != 0 or not isinstance(value, int):
        raise ProtocolError(f"field {field} has the wrong wire type")
    return value


def _bytes(mapping: dict[int, list[tuple[int, int | bytes]]], field: int, default: bytes = b"") -> bytes:
    values = mapping.get(field)
    if not values:
        return default
    wire_type, value = values[-1]
    if wire_type != 2 or not isinstance(value, bytes):
        raise ProtocolError(f"field {field} has the wrong wire type")
    return value


def _double(mapping: dict[int, list[tuple[int, int | bytes]]], field: int) -> float:
    values = mapping.get(field)
    if not values:
        return 0.0
    wire_type, value = values[-1]
    if wire_type != 1 or not isinstance(value, bytes):
        raise ProtocolError(f"field {field} has the wrong wire type")
    result = struct.unpack("<d", value)[0]
    if not math.isfinite(result):
        raise ProtocolError(f"field {field} must be finite")
    return result


def _touch_point(data: bytes) -> dict[str, int]:
    message = _message_map(data)
    point = {
        "pointerId": _varint(message, 1),
        "x": _varint(message, 2),
        "y": _varint(message, 3),
        "pressure": _varint(message, 4),
    }
    if point["x"] > 65535 or point["y"] > 65535 or point["pressure"] > 65535:
        raise ProtocolError("touch values must be normalized uint16 values")
    return point


def decode_envelope(data: bytes) -> EnvelopeMessage:
    if not isinstance(data, bytes) or len(data) > MAX_ENVELOPE_BYTES:
        raise ProtocolError("envelope is too large or not binary")
    root = _message_map(data)
    if _varint(root, 1) != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol version")
    sequence = _varint(root, 2)
    generation = _varint(root, 3)
    present = [field for field in range(10, 18) if field in root]
    if len(present) != 1:
        raise ProtocolError("envelope must contain exactly one client payload")
    field = present[0]
    nested_data = _bytes(root, field)
    nested = _message_map(nested_data)
    if field == 10:
        action = _varint(nested, 1)
        if action not in {1, 2, 3}:
            raise ProtocolError("invalid touch action")
        payload_type, payload = "touch", {"action": action, "point": _touch_point(_bytes(nested, 2))}
    elif field == 11:
        points = []
        for wire_type, value in nested.get(1, []):
            if wire_type != 2 or not isinstance(value, bytes):
                raise ProtocolError("invalid move point")
            points.append(_touch_point(value))
        if len(points) > 10:
            raise ProtocolError("at most ten pointers are supported")
        payload_type, payload = "touch_moves", {"points": points}
    elif field == 12:
        key = _varint(nested, 1)
        if key not in {1, 2, 3}:
            raise ProtocolError("invalid navigation key")
        payload_type, payload = "navigation", {"key": key}
    elif field == 13:
        payload_type, payload = "rotate", {}
    elif field == 14:
        mode = _varint(nested, 2)
        if mode not in {1, 2, 3, 4, 5}:
            raise ProtocolError("invalid quality mode")
        payload_type, payload = "quality_request", {"requestId": _varint(nested, 1), "mode": mode}
    elif field == 15:
        payload_type, payload = "receiver_stats", {
            "rttMs": max(0.0, _double(nested, 1)),
            "packetLoss": min(1.0, max(0.0, _double(nested, 2))),
            "playoutBufferMs": max(0.0, _double(nested, 3)),
            "freezeCount": _varint(nested, 4),
            "bytesReceived": _varint(nested, 5),
            "renderedFps": max(0.0, _double(nested, 6)),
            "availableBitrateBps": _varint(nested, 7),
            "jitterMs": max(0.0, _double(nested, 8)),
            "framesDropped": _varint(nested, 9),
            "incomingBitrateBps": _varint(nested, 10),
        }
    elif field == 16:
        raw_text = _bytes(nested, 1)
        if len(raw_text) > MAX_IME_TEXT_BYTES:
            raise ProtocolError("IME submission exceeds 16 KiB")
        try:
            text = raw_text.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolError("IME submission is not valid UTF-8") from exc
        payload_type, payload = "ime_submit", {"text": text, "editorAction": _varint(nested, 2)}
    else:
        payload_type, payload = "heartbeat", {"monotonicTimeMs": _varint(nested, 1)}
    return EnvelopeMessage(sequence, generation, payload_type, payload)


def _key(number: int, wire_type: int) -> bytes:
    return encode_varint((number << 3) | wire_type)


def _u(number: int, value: int) -> bytes:
    return _key(number, 0) + encode_varint(value)


def _b(number: int, value: bytes) -> bytes:
    return _key(number, 2) + encode_varint(len(value)) + value


def _s(number: int, value: str) -> bytes:
    return _b(number, value.encode("utf-8"))


def encode_server_envelope(sequence: int, video_generation: int, payload_type: str, payload: dict[str, Any]) -> bytes:
    if payload_type == "video_geometry":
        field = 30
        nested = _u(1, int(payload["width"])) + _u(2, int(payload["height"])) + _u(3, int(payload.get("rotation", 0)))
    elif payload_type == "quality_applied":
        field = 31
        nested = b"".join(
            [
                _u(1, int(payload["requestId"])), _u(2, int(payload["mode"])),
                _u(3, int(payload["width"])), _u(4, int(payload["height"])),
                _u(5, int(payload["fps"])), _u(6, int(payload["bitrateBps"])),
                _u(7, int(bool(payload.get("encoderRestarted", False)))),
            ]
        )
    elif payload_type == "ime_focus":
        field = 32
        nested = _u(1, int(bool(payload["active"]))) + _u(2, int(payload.get("inputType", 0)) & 0xFFFFFFFF) + _u(3, int(payload.get("imeOptions", 0)) & 0xFFFFFFFF) + _u(4, int(bool(payload.get("password", False))))
    elif payload_type == "session_error":
        field = 33
        nested = _s(1, str(payload["code"])[:80]) + _s(2, str(payload["message"])[:512]) + _u(3, int(bool(payload.get("fatal", False))))
    else:
        raise ProtocolError(f"unsupported server payload: {payload_type}")
    envelope = _u(1, PROTOCOL_VERSION) + _u(2, sequence) + _u(3, video_generation) + _b(field, nested)
    if len(envelope) > MAX_ENVELOPE_BYTES:
        raise ProtocolError("encoded envelope is too large")
    return envelope
