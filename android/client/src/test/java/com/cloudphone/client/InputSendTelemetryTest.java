package com.cloudphone.client;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class InputSendTelemetryTest {
    @Test public void criticalInputAlwaysRecordsItsActualSendTime() {
        InputSendTelemetry telemetry = new InputSendTelemetry(1_000L);

        InputSendTelemetry.Record down = telemetry.record(
                125L, 7L, 3, "touch_down", 1, false);
        InputSendTelemetry.Record up = telemetry.record(
                140L, 8L, 3, "touch_up", 1, false);

        assertTrue(down.shouldLog);
        assertTrue(up.shouldLog);
        assertEquals(125L, down.sentAtElapsedMs);
        assertEquals(140L, up.sentAtElapsedMs);
        assertEquals(8L, up.sequence);
        assertEquals(2L, up.totalMessages);
        assertEquals(2L, up.criticalMessages);
        assertEquals(0L, up.motionBatches);
    }

    @Test public void motionIsCountedContinuouslyButLoggedAtMostOncePerInterval() {
        InputSendTelemetry telemetry = new InputSendTelemetry(1_000L);

        InputSendTelemetry.Record first = telemetry.record(
                100L, 1L, 2, "touch_move", 2, true);
        InputSendTelemetry.Record suppressed = telemetry.record(
                1_099L, 2L, 2, "touch_move", 3, true);
        InputSendTelemetry.Record next = telemetry.record(
                1_100L, 3L, 2, "touch_move", 1, true);

        assertTrue(first.shouldLog);
        assertNull(suppressed);
        assertTrue(next.shouldLog);
        assertEquals(1_100L, next.sentAtElapsedMs);
        assertEquals(3L, next.totalMessages);
        assertEquals(3L, next.motionBatches);
        assertEquals(1, next.pointCount);
    }

    @Test(expected = IllegalArgumentException.class)
    public void rejectsNonPositiveMotionLogInterval() {
        new InputSendTelemetry(0L);
    }
}
