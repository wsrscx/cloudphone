package com.cloudphone.client;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.concurrent.TimeUnit;

public final class LatestGopQueueTest {
    @Test public void waitsForKeyFrameBeforeEmittingVideo() throws Exception {
        LatestGopQueue queue = new LatestGopQueue();
        assertFalse(queue.offer(video(1, 1, 0, false)));
        assertTrue(queue.offer(video(1, 2, 10_000, true)));
        assertEquals(2L, queue.poll(0, TimeUnit.MILLISECONDS).sequence);
        assertNull(queue.poll(0, TimeUnit.MILLISECONDS));
    }

    @Test public void overloadDropsWholeGopAndRequiresNextKeyFrame() throws Exception {
        LatestGopQueue queue = new LatestGopQueue(2, 1024, 1_000_000);
        assertTrue(queue.offer(video(1, 1, 0, true)));
        assertTrue(queue.offer(video(1, 2, 10_000, false)));
        assertFalse(queue.offer(video(1, 3, 20_000, false)));

        assertEquals(1L, queue.overloadCount());
        assertEquals(3L, queue.droppedFrames());
        assertEquals(0, queue.size());
        assertTrue(queue.isWaitingForKeyFrame());
        assertFalse(queue.offer(video(1, 4, 30_000, false)));
        assertTrue(queue.offer(video(1, 5, 40_000, true)));
        assertEquals(5L, queue.poll(0, TimeUnit.MILLISECONDS).sequence);
    }

    @Test public void resetStartsNewDecoderEpochAndRejectsOldGeneration() throws Exception {
        LatestGopQueue queue = new LatestGopQueue();
        assertTrue(queue.offer(reset(2, 10)));
        assertTrue(queue.offer(video(2, 11, 0, true)));
        assertTrue(queue.poll(0, TimeUnit.MILLISECONDS).isReset());
        assertEquals(11L, queue.poll(0, TimeUnit.MILLISECONDS).sequence);
        assertFalse(queue.offer(video(1, 99, 0, true)));
    }

    private static H264VideoPacket video(long generation, long sequence, long ptsUs,
                                         boolean keyFrame) {
        return new H264VideoPacket(H264VideoPacket.KIND_VIDEO, generation, sequence, ptsUs,
                1920, 1080, keyFrame, new byte[]{0, 0, 0, 1, 0x65});
    }

    private static H264VideoPacket reset(long generation, long sequence) {
        return new H264VideoPacket(H264VideoPacket.KIND_RESET, generation, sequence, 0,
                1920, 1080, false, new byte[0]);
    }
}
