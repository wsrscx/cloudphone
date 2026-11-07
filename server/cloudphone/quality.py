from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import IntEnum


class QualityMode(IntEnum):
    AUTO = 1
    P1080 = 2
    P720 = 3
    SMOOTH = 4
    # Appended for wire compatibility with clients which only know values 1-4.
    ORIGINAL = 5


@dataclass(frozen=True, slots=True)
class QualityProfile:
    mode: QualityMode
    width: int
    height: int
    fps: int
    minimum_bps: int
    default_bps: int
    maximum_bps: int

    @property
    def max_size(self) -> int:
        # scrcpy interprets max_size as a long-edge limit. 4096 preserves the
        # native resolution of current Android phones while retaining its
        # validated upper bound.
        return 4096 if self.mode == QualityMode.ORIGINAL else self.width


SMOOTH = QualityProfile(QualityMode.SMOOTH, 960, 540, 30, 1_200_000, 2_000_000, 3_000_000)
P720 = QualityProfile(QualityMode.P720, 1280, 720, 60, 2_500_000, 4_000_000, 6_000_000)
P1080 = QualityProfile(QualityMode.P1080, 1920, 1080, 60, 4_000_000, 7_000_000, 10_000_000)
ORIGINAL = QualityProfile(QualityMode.ORIGINAL, 0, 0, 60, 5_000_000, 9_000_000, 12_000_000)
BASE_PROFILES = (SMOOTH, P720, P1080, ORIGINAL)
AUTO_PROFILE_COUNT = 3
_SOFTWARE_OR_HYBRID_ENCODER_MARKERS = (
    "google", "android", "software", "ffmpeg", ".sw.", "hybrid",
)


@dataclass(frozen=True, slots=True)
class EncoderCapabilities:
    """Relevant limits advertised by the device's selected H.264 encoder."""

    max_bitrate_bps: int = 0
    blocks_per_second: int = 0
    block_width: int = 16
    block_height: int = 16
    native_width: int = 0
    native_height: int = 0
    encoder_name: str = ""

    def constrain(self, profile: QualityProfile) -> QualityProfile:
        bitrate_limit = max(0, self.max_bitrate_bps)
        is_software_or_hybrid = any(
            marker in self.encoder_name.lower()
            for marker in _SOFTWARE_OR_HYBRID_ENCODER_MARKERS
        )
        minimum_bps = min(profile.minimum_bps, bitrate_limit) if bitrate_limit else profile.minimum_bps
        default_bps = min(profile.default_bps, bitrate_limit) if bitrate_limit else profile.default_bps
        maximum_bps = min(profile.maximum_bps, bitrate_limit) if bitrate_limit else profile.maximum_bps

        width, height = profile.width, profile.height
        if profile.mode == QualityMode.ORIGINAL:
            width = self.native_width or 1920
            height = self.native_height or 1080
        fps = profile.fps
        if self.blocks_per_second > 0 and width > 0 and height > 0:
            block_width = max(1, self.block_width)
            block_height = max(1, self.block_height)
            frame_blocks = math.ceil(width / block_width) * math.ceil(height / block_height)
            fps = max(1, min(fps, self.blocks_per_second // max(1, frame_blocks)))
        if is_software_or_hybrid:
            fps = min(fps, 30)

        return replace(
            profile,
            width=width,
            height=height,
            fps=fps,
            minimum_bps=minimum_bps,
            default_bps=max(minimum_bps, default_bps),
            maximum_bps=max(default_bps, maximum_bps),
        )


@dataclass(frozen=True, slots=True)
class ReceiverSample:
    rtt_ms: float
    packet_loss: float
    jitter_ms: float
    freeze_count: int
    available_bitrate_bps: int
    rendered_fps: float = 0.0
    frames_dropped: int = 0
    playout_delay_ms: float = 0.0
    incoming_bitrate_bps: int = 0


@dataclass(frozen=True, slots=True)
class QualityDecision:
    mode: QualityMode
    profile: QualityProfile
    bitrate_bps: int
    encoder_restart: bool
    reason: str


@dataclass(frozen=True, slots=True)
class AdaptiveQualityState:
    values: tuple[object, ...]


class AdaptiveQualityController:
    """Capability-aware auto quality controller with congestion hysteresis."""

    STARTUP_GRACE_SECONDS = 3.0
    CONGESTION_CONFIRM_SECONDS = 2.0
    SEVERE_CONGESTION_CONFIRM_SECONDS = 0.75
    BITRATE_ADJUSTMENT_INTERVAL_SECONDS = 2.0
    PROFILE_DOWNGRADE_SECONDS = 5.0
    RECOVERY_INTERVAL_SECONDS = 4.0
    AUTO_UPGRADE_STABLE_SECONDS = 15.0
    SOURCE_MATCH_UPGRADE_SECONDS = 8.0

    _STATE_FIELDS = (
        "mode", "profile_index", "bitrate_bps", "loss_ewma", "rtt_ewma", "jitter_ewma",
        "rendered_fps_ewma", "frame_drop_ewma", "playout_delay_ewma", "incoming_bitrate_ewma",
        "_last_sample_at", "_last_freeze_count", "_stable_since", "_upgrade_blocked_until",
        "_congested_since", "_congested_at_floor_since", "_last_adjustment_at",
        "_startup_grace_until",
    )

    def __init__(self, now: float = 0.0, capabilities: EncoderCapabilities | None = None):
        self.capabilities = capabilities or EncoderCapabilities()
        self.profiles = tuple(self.capabilities.constrain(profile) for profile in BASE_PROFILES)
        # Start from the profile with the smallest encoder and network footprint.
        # A clean link upgrades quickly; a weak decoder no longer spends its
        # first tens of seconds recovering from an optimistic 1080p/60 start.
        self.auto_start_index = 0
        self.mode = QualityMode.AUTO
        self.profile_index = self.auto_start_index
        self.bitrate_bps = self.profile.default_bps
        self.loss_ewma = 0.0
        self.rtt_ewma = 0.0
        self.jitter_ewma = 0.0
        self.rendered_fps_ewma = 0.0
        self.frame_drop_ewma = 0.0
        self.playout_delay_ewma = 0.0
        self.incoming_bitrate_ewma = 0.0
        self._last_sample_at: float | None = None
        self._last_freeze_count = 0
        self._stable_since: float | None = None
        self._upgrade_blocked_until = now
        self._congested_since: float | None = None
        self._congested_at_floor_since: float | None = None
        self._last_adjustment_at = now
        self._startup_grace_until = now + self.STARTUP_GRACE_SECONDS

    @property
    def profile(self) -> QualityProfile:
        return self.profiles[self.profile_index]

    def snapshot(self) -> AdaptiveQualityState:
        return AdaptiveQualityState(tuple(getattr(self, field) for field in self._STATE_FIELDS))

    def restore(self, state: AdaptiveQualityState) -> None:
        for field, value in zip(self._STATE_FIELDS, state.values, strict=True):
            setattr(self, field, value)

    def begin_stream(self, now: float) -> None:
        """Start adaptation timing when receiver media actually becomes live."""
        self.loss_ewma = 0.0
        self.rtt_ewma = 0.0
        self.jitter_ewma = 0.0
        self.rendered_fps_ewma = 0.0
        self.frame_drop_ewma = 0.0
        self.playout_delay_ewma = 0.0
        self.incoming_bitrate_ewma = 0.0
        self._last_sample_at = None
        self._last_freeze_count = 0
        self._stable_since = None
        self._congested_since = None
        self._congested_at_floor_since = None
        self._last_adjustment_at = now
        self._startup_grace_until = now + self.STARTUP_GRACE_SECONDS

    def set_mode(self, mode: QualityMode, now: float) -> QualityDecision:
        self.mode = QualityMode(mode)
        target_index = self.auto_start_index if mode == QualityMode.AUTO else {
            QualityMode.P1080: 2,
            QualityMode.P720: 1,
            QualityMode.SMOOTH: 0,
            QualityMode.ORIGINAL: 3,
        }[mode]
        old = self.profile
        self.profile_index = target_index
        target = self.profile
        self.bitrate_bps = target.default_bps
        self._upgrade_blocked_until = now
        self.begin_stream(now)
        return QualityDecision(mode, target, self.bitrate_bps, target != old, "mode-selected")

    def synchronize_encoder(self, bitrate_bps: int, max_size: int) -> None:
        """Align controller state with the parameters acknowledged by the device encoder."""
        for index, profile in enumerate(self.profiles):
            if profile.max_size == max_size:
                self.profile_index = index
                break
        self.bitrate_bps = min(self.profile.maximum_bps, max(128_000, bitrate_bps))

    def observe(self, sample: ReceiverSample, now: float) -> QualityDecision | None:
        if self.mode != QualityMode.AUTO:
            # Manual modes, especially ORIGINAL, are explicit quality promises.
            self._last_sample_at = now
            self._last_freeze_count = max(self._last_freeze_count, sample.freeze_count)
            return None
        if now < self._startup_grace_until:
            self._last_sample_at = now
            self._last_freeze_count = max(self._last_freeze_count, sample.freeze_count)
            return None

        dt = 1.0 if self._last_sample_at is None else max(0.001, min(10.0, now - self._last_sample_at))
        self._last_sample_at = now
        alpha = 1.0 - math.exp(-dt / 3.0)
        self.loss_ewma += alpha * (sample.packet_loss - self.loss_ewma)
        self.rtt_ewma += alpha * (sample.rtt_ms - self.rtt_ewma)
        self.jitter_ewma += alpha * (sample.jitter_ms - self.jitter_ewma)
        rendered_fps = max(0.0, sample.rendered_fps)
        frames_dropped = max(0, sample.frames_dropped)
        if rendered_fps > 0.0:
            self.rendered_fps_ewma += alpha * (rendered_fps - self.rendered_fps_ewma)
        observed_frames = rendered_fps + frames_dropped
        if observed_frames > 0.0:
            frame_drop_fraction = frames_dropped / observed_frames
            self.frame_drop_ewma += alpha * (frame_drop_fraction - self.frame_drop_ewma)
        if sample.playout_delay_ms > 0.0:
            self.playout_delay_ewma += alpha * (sample.playout_delay_ms - self.playout_delay_ewma)
        if sample.incoming_bitrate_bps > 0:
            self.incoming_bitrate_ewma += alpha * (
                sample.incoming_bitrate_bps - self.incoming_bitrate_ewma
            )
        froze = sample.freeze_count > self._last_freeze_count
        self._last_freeze_count = max(self._last_freeze_count, sample.freeze_count)

        bandwidth_low = (
            sample.available_bitrate_bps > 0
            and sample.available_bitrate_bps < int(self.bitrate_bps * 0.85)
        )
        severe = (
            froze or sample.packet_loss >= 0.10 or self.loss_ewma >= 0.08
            or self.frame_drop_ewma >= 0.10
            or sample.playout_delay_ms >= 140.0 or self.playout_delay_ewma >= 120.0
        )
        congested = (
            severe or bandwidth_low or self.loss_ewma > 0.03
            or self.rtt_ewma > 120.0 or self.jitter_ewma > 30.0
            or self.frame_drop_ewma > 0.03 or self.playout_delay_ewma > 70.0
        )

        if congested:
            self._stable_since = None
            if self._congested_since is None:
                self._congested_since = now
            confirmation = (
                self.SEVERE_CONGESTION_CONFIRM_SECONDS if severe else self.CONGESTION_CONFIRM_SECONDS
            )
            if now - self._congested_since < confirmation:
                return None
            if (
                severe
                and self.profile_index > 0
                and now - self._last_adjustment_at >= self.BITRATE_ADJUSTMENT_INTERVAL_SECONDS
            ):
                self.profile_index -= 1
                self.bitrate_bps = self.profile.default_bps
                self._upgrade_blocked_until = now + 30.0
                self.begin_stream(now)
                return QualityDecision(
                    self.mode, self.profile, self.bitrate_bps, True, "latency-downgrade"
                )
            if (
                self.bitrate_bps > self.profile.minimum_bps
                and now - self._last_adjustment_at >= self.BITRATE_ADJUSTMENT_INTERVAL_SECONDS
            ):
                factor = 0.80 if severe else 0.90
                candidate = max(self.profile.minimum_bps, int(self.bitrate_bps * factor))
                if candidate < self.bitrate_bps:
                    self.bitrate_bps = candidate
                    self._last_adjustment_at = now
                    if candidate == self.profile.minimum_bps:
                        self._congested_at_floor_since = now
                    return QualityDecision(
                        self.mode, self.profile, candidate, False,
                        "severe-congestion" if severe else "congestion",
                    )
            if self.bitrate_bps <= self.profile.minimum_bps:
                if self._congested_at_floor_since is None:
                    self._congested_at_floor_since = now
                if (
                    self.profile_index > 0
                    and now - self._congested_at_floor_since >= self.PROFILE_DOWNGRADE_SECONDS
                ):
                    self.profile_index -= 1
                    self.bitrate_bps = self.profile.default_bps
                    self._upgrade_blocked_until = now + 20.0
                    self.begin_stream(now)
                    return QualityDecision(self.mode, self.profile, self.bitrate_bps, True, "profile-downgrade")
            return None

        self._congested_since = None
        self._congested_at_floor_since = None
        if self._stable_since is None:
            self._stable_since = now

        bandwidth_unknown = sample.available_bitrate_bps <= 0
        if (
            self.bitrate_bps < self.profile.default_bps
            and now - self._stable_since >= self.RECOVERY_INTERVAL_SECONDS
            and now - self._last_adjustment_at >= self.RECOVERY_INTERVAL_SECONDS
            and (
                bandwidth_unknown
                or sample.available_bitrate_bps >= int(self.bitrate_bps * 1.10)
            )
        ):
            grown = min(self.profile.default_bps, max(self.bitrate_bps + 250_000, int(self.bitrate_bps * 1.15)))
            if grown > self.bitrate_bps:
                self.bitrate_bps = grown
                self._last_adjustment_at = now
                return QualityDecision(self.mode, self.profile, grown, False, "stable-recovery")

        next_profile = (
            self.profiles[self.profile_index + 1]
            if self.profile_index < AUTO_PROFILE_COUNT - 1 else None
        )
        source_matches_next_profile = bool(
            next_profile
            and self.frame_drop_ewma < 0.02
            and next_profile.fps * 0.75 <= self.rendered_fps_ewma <= next_profile.fps * 1.10
        )
        upgrade_stable_seconds = (
            self.SOURCE_MATCH_UPGRADE_SECONDS
            if source_matches_next_profile else self.AUTO_UPGRADE_STABLE_SECONDS
        )
        if (
            self.profile_index < AUTO_PROFILE_COUNT - 1
            and now >= self._upgrade_blocked_until
            and now - self._stable_since >= upgrade_stable_seconds
        ):
            assert next_profile is not None
            preserves_motion = (
                self.profile.fps < 50 or next_profile.fps >= 50 or source_matches_next_profile
            )
            bandwidth_sufficient = (
                bandwidth_unknown
                or sample.available_bitrate_bps >= int(next_profile.minimum_bps * 1.20)
            )
            if preserves_motion and bandwidth_sufficient:
                self.profile_index += 1
                self.bitrate_bps = self.profile.default_bps
                self._upgrade_blocked_until = now + 20.0
                self.begin_stream(now)
                return QualityDecision(self.mode, self.profile, self.bitrate_bps, True, "profile-upgrade")
        return None
