from __future__ import annotations

import unittest

from cloudphone.devices import AdbResult
from cloudphone.video_capabilities import (
    parse_h264_encoder_limits,
    probe_h264_encoder_capabilities,
    select_h264_encoder_limits,
)


CODECS = """<?xml version="1.0" encoding="utf-8"?>
<Included>
  <Encoders>
    <MediaCodec name="OMX.google.h264.encoder" type="video/avc">
      <Limit name="block-size" value="16x16" />
      <Limit name="blocks-per-second" range="1-245760" />
      <Limit name="bitrate" range="1-12000000" />
    </MediaCodec>
  </Encoders>
</Included>
"""


class _FakeAdb:
    async def shell(self, _serial: str, *args: str, **_kwargs: object) -> AdbResult:
        if args[0] == "find":
            return AdbResult(b"/system/etc/media_codecs_google_video.xml\n", b"", 0)
        if args[0] == "cat":
            return AdbResult(CODECS.encode(), b"", 0)
        if args[:2] == ("wm", "size"):
            return AdbResult(b"Physical size: 1080x1920\n", b"", 0)
        raise AssertionError(args)


class VideoCapabilitiesTests(unittest.IsolatedAsyncioTestCase):
    def test_parses_h264_encoder_limits(self) -> None:
        selected = select_h264_encoder_limits(parse_h264_encoder_limits(CODECS))
        self.assertIsNotNone(selected)
        self.assertEqual(selected.name, "OMX.google.h264.encoder")  # type: ignore[union-attr]
        self.assertEqual(selected.max_bitrate_bps, 12_000_000)  # type: ignore[union-attr]
        self.assertEqual(selected.blocks_per_second, 245_760)  # type: ignore[union-attr]

    async def test_probe_combines_codec_and_native_display_limits(self) -> None:
        capabilities = await probe_h264_encoder_capabilities(_FakeAdb(), "serial")  # type: ignore[arg-type]
        self.assertEqual(capabilities.encoder_name, "OMX.google.h264.encoder")
        self.assertEqual(capabilities.max_bitrate_bps, 12_000_000)
        self.assertEqual(capabilities.blocks_per_second, 245_760)
        self.assertEqual((capabilities.native_width, capabilities.native_height), (1080, 1920))
