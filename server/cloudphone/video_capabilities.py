from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .devices import AdbRunner
from .quality import EncoderCapabilities


_CODEC_FILE = re.compile(
    r"^/(?:system|vendor|odm|product|system_ext)/etc/media_codecs[^/]*\.xml$"
)
_SIZE = re.compile(r"(?P<width>\d+)x(?P<height>\d+)")
_SOFTWARE_ENCODER = re.compile(r"(?:google|android|software|ffmpeg|\.sw\.)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class _CodecLimits:
    name: str
    max_bitrate_bps: int = 0
    blocks_per_second: int = 0
    block_width: int = 16
    block_height: int = 16


def _tag_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _maximum(value: str) -> int:
    numbers = [int(item) for item in re.findall(r"\d+", value)]
    return max(numbers, default=0)


def _dimensions(value: str, fallback: tuple[int, int] = (16, 16)) -> tuple[int, int]:
    match = _SIZE.search(value)
    return (int(match.group("width")), int(match.group("height"))) if match else fallback


def parse_h264_encoder_limits(document: str) -> list[_CodecLimits]:
    try:
        root = ET.fromstring(document)
    except ET.ParseError:
        return []
    found: list[_CodecLimits] = []
    for encoders in root.iter():
        if _tag_name(encoders.tag) != "Encoders":
            continue
        for codec in encoders.iter():
            if _tag_name(codec.tag) != "MediaCodec":
                continue
            codec_type = codec.attrib.get("type", "")
            matching_types = [
                child for child in codec
                if _tag_name(child.tag) == "Type" and child.attrib.get("name") == "video/avc"
            ]
            if codec_type != "video/avc" and not matching_types:
                continue
            limits = [child for child in codec if _tag_name(child.tag) == "Limit"]
            for matching_type in matching_types:
                limits.extend(child for child in matching_type if _tag_name(child.tag) == "Limit")
            values = {limit.attrib.get("name", ""): limit.attrib for limit in limits}
            block_width, block_height = _dimensions(values.get("block-size", {}).get("value", ""))
            found.append(
                _CodecLimits(
                    codec.attrib.get("name", "unknown-h264-encoder"),
                    _maximum(values.get("bitrate", {}).get("range", "")),
                    _maximum(values.get("blocks-per-second", {}).get("range", "")),
                    block_width,
                    block_height,
                )
            )
    return found


def select_h264_encoder_limits(candidates: list[_CodecLimits]) -> _CodecLimits | None:
    if not candidates:
        return None
    merged: dict[str, _CodecLimits] = {}
    for candidate in candidates:
        previous = merged.get(candidate.name)
        if previous is None:
            merged[candidate.name] = candidate
            continue
        merged[candidate.name] = _CodecLimits(
            candidate.name,
            max(previous.max_bitrate_bps, candidate.max_bitrate_bps),
            max(previous.blocks_per_second, candidate.blocks_per_second),
            candidate.block_width or previous.block_width,
            candidate.block_height or previous.block_height,
        )
    return max(
        merged.values(),
        key=lambda value: (
            not bool(_SOFTWARE_ENCODER.search(value.name)),
            value.blocks_per_second,
            value.max_bitrate_bps,
        ),
    )


async def probe_h264_encoder_capabilities(adb: AdbRunner, serial: str) -> EncoderCapabilities:
    roots = ("/system/etc", "/vendor/etc", "/odm/etc", "/product/etc", "/system_ext/etc")
    listing = await adb.shell(
        serial, "find", *roots, "-maxdepth", "1", "-type", "f", "-name", "media_codecs*.xml", "-print",
        timeout=8.0, check=False,
    )
    paths = sorted({line.strip() for line in listing.text.splitlines() if _CODEC_FILE.fullmatch(line.strip())})
    candidates: list[_CodecLimits] = []
    for path in paths:
        content = await adb.shell(serial, "cat", path, timeout=5.0, check=False)
        if content.returncode == 0:
            candidates.extend(parse_h264_encoder_limits(content.text))
    selected = select_h264_encoder_limits(candidates)

    size_result = await adb.shell(serial, "wm", "size", timeout=5.0, check=False)
    sizes = [(int(match.group("width")), int(match.group("height"))) for match in _SIZE.finditer(size_result.text)]
    native_width, native_height = sizes[-1] if sizes else (0, 0)
    if selected is None:
        return EncoderCapabilities(native_width=native_width, native_height=native_height)
    return EncoderCapabilities(
        max_bitrate_bps=selected.max_bitrate_bps,
        blocks_per_second=selected.blocks_per_second,
        block_width=selected.block_width,
        block_height=selected.block_height,
        native_width=native_width,
        native_height=native_height,
        encoder_name=selected.name,
    )
