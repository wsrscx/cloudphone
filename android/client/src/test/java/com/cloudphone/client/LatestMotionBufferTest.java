package com.cloudphone.client;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertSame;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

import org.junit.Test;

public final class LatestMotionBufferTest {
    @Test public void failedSendRetainsLatestBatchForRetry() {
        LatestMotionBuffer buffer = new LatestMotionBuffer();
        buffer.replace(Collections.singletonList(pointer(1, 100)));
        List<ControlProtocol.Pointer> attempted = buffer.peek();

        buffer.complete(attempted, false);

        assertSame(attempted, buffer.peek());
    }

    @Test public void newerBatchSupersedesAnUnsentBatch() {
        LatestMotionBuffer buffer = new LatestMotionBuffer();
        buffer.replace(Collections.singletonList(pointer(1, 100)));
        List<ControlProtocol.Pointer> oldAttempt = buffer.peek();
        buffer.replace(Collections.singletonList(pointer(1, 900)));

        buffer.complete(oldAttempt, true);

        assertNotNull(buffer.peek());
        assertEquals(900, buffer.peek().get(0).x);
    }

    @Test public void successfulSendClearsOnlyTheAttemptedLatestBatch() {
        LatestMotionBuffer buffer = new LatestMotionBuffer();
        buffer.replace(Arrays.asList(pointer(1, 100), pointer(2, 200)));
        List<ControlProtocol.Pointer> attempted = buffer.peek();

        buffer.complete(attempted, true);

        assertNull(buffer.peek());
    }

    @Test public void pointerUpRemovesOnlyThatPointersPendingMove() {
        LatestMotionBuffer buffer = new LatestMotionBuffer();
        buffer.replace(Arrays.asList(pointer(1, 100), pointer(2, 200)));

        buffer.removePointer(1);

        assertNotNull(buffer.peek());
        assertEquals(1, buffer.peek().size());
        assertEquals(2, buffer.peek().get(0).id);
    }

    private static ControlProtocol.Pointer pointer(long id, int x) {
        return new ControlProtocol.Pointer(id, x, x, 32_768);
    }
}
