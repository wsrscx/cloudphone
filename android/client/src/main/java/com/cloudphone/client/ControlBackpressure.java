package com.cloudphone.client;

/** Queue limits for replaceable motion and optional telemetry messages. */
final class ControlBackpressure {
    static final long MAX_MOTION_BUFFERED_BYTES = 0L;
    static final long MAX_TELEMETRY_BUFFERED_BYTES = 0L;

    private ControlBackpressure() {}

    static boolean canSendMotion(long queuedBytes) {
        return queuedBytes <= MAX_MOTION_BUFFERED_BYTES;
    }

    static boolean canSendTelemetry(long queuedBytes) {
        return queuedBytes <= MAX_TELEMETRY_BUFFERED_BYTES;
    }
}
