from __future__ import annotations

import unittest

from cloudphone.quality import (
    AdaptiveQualityController,
    EncoderCapabilities,
    QualityMode,
    ReceiverSample,
)


def sample(
    *, loss: float = 0.0, rtt: float = 20.0, jitter: float = 5.0,
    freezes: int = 0, bandwidth: int = 0, fps: float = 60.0, dropped: int = 0,
    playout: float = 0.0, incoming: int = 0,
) -> ReceiverSample:
    return ReceiverSample(rtt, loss, jitter, freezes, bandwidth, fps, dropped, playout, incoming)


class QualityControllerTests(unittest.TestCase):
    def test_auto_starts_with_latency_first_profile(self) -> None:
        controller = AdaptiveQualityController()
        self.assertEqual((controller.profile.width, controller.profile.height), (960, 540))
        self.assertEqual(controller.profile.fps, 30)
        self.assertEqual(controller.bitrate_bps, 2_000_000)

    def test_software_encoder_caps_every_profile_to_advertised_limits(self) -> None:
        capabilities = EncoderCapabilities(
            max_bitrate_bps=12_000_000,
            blocks_per_second=245_760,
            block_width=16,
            block_height=16,
            native_width=1080,
            native_height=1920,
            encoder_name="OMX.google.h264.encoder",
        )
        controller = AdaptiveQualityController(capabilities=capabilities)
        self.assertEqual((controller.profile.width, controller.profile.height), (960, 540))
        self.assertEqual(controller.profile.fps, 30)
        for profile in controller.profiles:
            self.assertLessEqual(profile.fps, 30)
            self.assertLessEqual(profile.minimum_bps, 12_000_000)
            self.assertLessEqual(profile.default_bps, 12_000_000)
            self.assertLessEqual(profile.maximum_bps, 12_000_000)

        original = controller.set_mode(QualityMode.ORIGINAL, 1.0)
        self.assertEqual((original.profile.width, original.profile.height), (1080, 1920))
        self.assertEqual(original.profile.fps, 30)
        self.assertEqual(original.bitrate_bps, 9_000_000)

    def test_hybrid_encoder_is_limited_to_30_fps_and_detected_bitrate(self) -> None:
        controller = AdaptiveQualityController(
            capabilities=EncoderCapabilities(
                max_bitrate_bps=8_000_000,
                encoder_name="vendor.avc.encoder.hybrid",
            )
        )

        for profile in controller.profiles:
            self.assertLessEqual(profile.fps, 30)
            self.assertLessEqual(profile.maximum_bps, 8_000_000)

    def test_any_positive_detected_bitrate_is_a_hard_limit(self) -> None:
        controller = AdaptiveQualityController(
            capabilities=EncoderCapabilities(max_bitrate_bps=64_000)
        )

        for profile in controller.profiles:
            self.assertEqual(profile.minimum_bps, 64_000)
            self.assertEqual(profile.default_bps, 64_000)
            self.assertEqual(profile.maximum_bps, 64_000)
        controller.synchronize_encoder(50_000_000, 960)
        self.assertEqual(controller.bitrate_bps, 64_000)

    def test_one_short_loss_spike_does_not_reduce_quality(self) -> None:
        controller = AdaptiveQualityController()
        self.assertIsNone(controller.observe(sample(loss=0.2), 6.1))
        self.assertIsNone(controller.observe(sample(), 6.5))
        self.assertEqual(controller.bitrate_bps, 2_000_000)

    def test_confirmed_severe_loss_reduces_bitrate(self) -> None:
        controller = AdaptiveQualityController()
        self.assertIsNone(controller.observe(sample(loss=0.2), 6.1))
        decision = controller.observe(sample(loss=0.2), 7.2)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.bitrate_bps, 1_600_000)  # type: ignore[union-attr]

    def test_persistent_floor_congestion_downgrades_auto_profile(self) -> None:
        controller = AdaptiveQualityController()
        controller.synchronize_encoder(6_000_000, 1920)
        controller.begin_stream(0.0)
        self.assertIsNone(controller.observe(sample(loss=0.2), 6.1))
        decision = controller.observe(sample(loss=0.2), 7.2)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.reason, "latency-downgrade")  # type: ignore[union-attr]
        self.assertEqual(controller.profile.height, 720)

    def test_unknown_bandwidth_can_recover_bitrate(self) -> None:
        controller = AdaptiveQualityController()
        controller.synchronize_encoder(6_000_000, 1920)
        controller.begin_stream(0.0)
        controller.observe(sample(), 6.1)
        decision = controller.observe(sample(), 10.2)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.reason, "stable-recovery")  # type: ignore[union-attr]
        self.assertGreater(controller.bitrate_bps, 6_000_000)

    def test_unknown_bandwidth_can_upgrade_after_stable_link(self) -> None:
        controller = AdaptiveQualityController()
        controller.synchronize_encoder(8_000_000, 1280)
        controller.begin_stream(0.0)
        controller.observe(sample(), 6.1)
        decision = controller.observe(sample(), 22.2)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.reason, "profile-upgrade")  # type: ignore[union-attr]
        self.assertEqual(controller.profile.height, 1080)

    def test_stable_30_fps_content_prefers_sharper_1080p30(self) -> None:
        capabilities = EncoderCapabilities(
            max_bitrate_bps=12_000_000,
            blocks_per_second=245_760,
            native_width=1080,
            native_height=1920,
        )
        controller = AdaptiveQualityController(capabilities=capabilities)
        decision = None
        for second in range(7, 46):
            decision = controller.observe(sample(fps=30.0), float(second)) or decision
        self.assertIsNotNone(decision)
        self.assertEqual(decision.reason, "profile-upgrade")  # type: ignore[union-attr]
        self.assertEqual(controller.profile.height, 1080)
        self.assertEqual(controller.profile.fps, 30)

    def test_60_fps_content_stays_at_smoother_720p60(self) -> None:
        capabilities = EncoderCapabilities(
            max_bitrate_bps=12_000_000,
            blocks_per_second=245_760,
            native_width=1080,
            native_height=1920,
        )
        controller = AdaptiveQualityController(capabilities=capabilities)
        for second in range(7, 30):
            controller.observe(sample(fps=60.0), float(second))
        self.assertEqual(controller.profile.height, 720)

    def test_persistent_receiver_frame_drops_reduce_bitrate(self) -> None:
        controller = AdaptiveQualityController()
        decision = None
        for second in range(7, 15):
            decision = controller.observe(sample(fps=52.0, dropped=8), float(second)) or decision
        self.assertIsNotNone(decision)
        self.assertLess(controller.bitrate_bps, controller.profile.default_bps)

    def test_low_source_fps_without_drops_is_not_congestion(self) -> None:
        controller = AdaptiveQualityController()
        for second in range(7, 15):
            self.assertIsNone(controller.observe(sample(fps=30.0), float(second)))
        self.assertEqual(controller.bitrate_bps, controller.profile.default_bps)

    def test_manual_original_never_changes_bitrate_under_congestion(self) -> None:
        controller = AdaptiveQualityController()
        controller.set_mode(QualityMode.ORIGINAL, 0.0)
        expected = controller.bitrate_bps
        for second in range(7, 27):
            self.assertIsNone(controller.observe(sample(loss=0.2), float(second)))
        self.assertEqual(controller.bitrate_bps, expected)

    def test_encoder_acknowledgement_resynchronizes_and_clamps_state(self) -> None:
        controller = AdaptiveQualityController()
        controller.set_mode(QualityMode.P1080, 0.0)
        controller.synchronize_encoder(50_000_000, 1280)
        self.assertEqual(controller.profile.height, 720)
        self.assertEqual(controller.bitrate_bps, controller.profile.maximum_bps)

    def test_failed_change_can_restore_controller_state(self) -> None:
        controller = AdaptiveQualityController()
        before = controller.snapshot()
        controller.set_mode(QualityMode.ORIGINAL, 10.0)
        controller.restore(before)
        self.assertEqual(controller.mode, QualityMode.AUTO)
        self.assertEqual(controller.profile.height, 540)
        self.assertEqual(controller.bitrate_bps, 2_000_000)

    def test_stream_grace_starts_at_media_connection(self) -> None:
        controller = AdaptiveQualityController()
        controller.begin_stream(30.0)
        self.assertIsNone(controller.observe(sample(loss=0.5, freezes=3), 32.0))
        self.assertEqual(controller.bitrate_bps, 2_000_000)

    def test_playout_delay_causes_fast_profile_downgrade(self) -> None:
        controller = AdaptiveQualityController()
        controller.synchronize_encoder(8_000_000, 1280)
        controller.begin_stream(0.0)
        self.assertIsNone(controller.observe(sample(playout=180.0), 3.1))
        decision = controller.observe(sample(playout=180.0), 4.0)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.reason, "latency-downgrade")  # type: ignore[union-attr]
        self.assertEqual(controller.profile.height, 540)

    def test_original_mode_uses_native_size_limit_and_bounded_bitrate(self) -> None:
        controller = AdaptiveQualityController()
        decision = controller.set_mode(QualityMode.ORIGINAL, 10.0)
        self.assertEqual(decision.profile.max_size, 4096)
        self.assertEqual(decision.bitrate_bps, 9_000_000)
