package com.cloudphone.client;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class ControlBackpressureTest {
    @Test public void dropsReplaceableTrafficBehindQueuedCriticalInput() {
        assertTrue(ControlBackpressure.canSendMotion(ControlBackpressure.MAX_MOTION_BUFFERED_BYTES));
        assertFalse(ControlBackpressure.canSendMotion(ControlBackpressure.MAX_MOTION_BUFFERED_BYTES + 1));
        assertTrue(ControlBackpressure.canSendTelemetry(
                ControlBackpressure.MAX_TELEMETRY_BUFFERED_BYTES));
        assertFalse(ControlBackpressure.canSendTelemetry(
                ControlBackpressure.MAX_TELEMETRY_BUFFERED_BYTES + 1));
    }
}
