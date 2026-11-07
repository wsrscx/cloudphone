package com.cloudphone.client;

import java.util.ArrayDeque;
import java.util.Deque;
import java.util.concurrent.TimeUnit;

/** Bounded decoder input which drops an entire stale GOP after overload. */
final class LatestGopQueue implements AutoCloseable {
    static final int DEFAULT_MAX_FRAMES = 6;
    static final int DEFAULT_MAX_BYTES = 4 * 1024 * 1024;
    static final long DEFAULT_MAX_SPAN_US = 150_000L;

    private final int maxFrames;
    private final int maxBytes;
    private final long maxSpanUs;
    private final Deque<H264VideoPacket> packets = new ArrayDeque<>();
    private int bytes;
    private boolean closed;
    private boolean waitingForKeyFrame = true;
    private long activeGeneration = -1L;
    private long lastSequence = -1L;
    private long droppedFrames;
    private long overloadCount;

    LatestGopQueue() {
        this(DEFAULT_MAX_FRAMES, DEFAULT_MAX_BYTES, DEFAULT_MAX_SPAN_US);
    }

    LatestGopQueue(int maxFrames, int maxBytes, long maxSpanUs) {
        if (maxFrames < 1 || maxBytes < 1 || maxSpanUs < 1L) {
            throw new IllegalArgumentException("queue limits must be positive");
        }
        this.maxFrames = maxFrames;
        this.maxBytes = maxBytes;
        this.maxSpanUs = maxSpanUs;
    }

    synchronized boolean offer(H264VideoPacket packet) {
        if (closed) return false;
        if (packet.generation < activeGeneration) {
            if (packet.kind == H264VideoPacket.KIND_VIDEO) droppedFrames++;
            return false;
        }
        if (packet.generation > activeGeneration) {
            dropQueuedFrames();
            activeGeneration = packet.generation;
            lastSequence = -1L;
            waitingForKeyFrame = true;
        }
        if (lastSequence >= 0L && packet.sequence <= lastSequence) {
            if (packet.kind == H264VideoPacket.KIND_VIDEO) droppedFrames++;
            return false;
        }
        lastSequence = packet.sequence;
        if (packet.isReset()) {
            dropQueuedFrames();
            waitingForKeyFrame = true;
            append(packet);
            return true;
        }
        if (waitingForKeyFrame) {
            if (!packet.keyFrame) {
                droppedFrames++;
                return false;
            }
            waitingForKeyFrame = false;
        }
        append(packet);
        if (!overLimit()) return true;

        overloadCount++;
        dropQueuedFrames();
        waitingForKeyFrame = true;
        return false;
    }

    synchronized H264VideoPacket poll(long timeout, TimeUnit unit) throws InterruptedException {
        long remainingNanos = unit.toNanos(timeout);
        long deadline = System.nanoTime() + remainingNanos;
        while (packets.isEmpty() && !closed) {
            if (remainingNanos <= 0L) return null;
            TimeUnit.NANOSECONDS.timedWait(this, remainingNanos);
            remainingNanos = deadline - System.nanoTime();
        }
        if (packets.isEmpty()) return null;
        H264VideoPacket packet = packets.removeFirst();
        bytes -= packet.payload.length;
        return packet;
    }

    synchronized void resetForDecoderRecovery() {
        if (closed) return;
        dropQueuedFrames();
        waitingForKeyFrame = true;
        notifyAll();
    }

    synchronized boolean isWaitingForKeyFrame() {
        return waitingForKeyFrame;
    }

    synchronized long droppedFrames() {
        return droppedFrames;
    }

    synchronized long overloadCount() {
        return overloadCount;
    }

    synchronized int size() {
        return packets.size();
    }

    @Override public synchronized void close() {
        closed = true;
        packets.clear();
        bytes = 0;
        notifyAll();
    }

    private void append(H264VideoPacket packet) {
        packets.addLast(packet);
        bytes += packet.payload.length;
        notifyAll();
    }

    private boolean overLimit() {
        int videoFrames = 0;
        long firstPts = 0L;
        long lastPts = 0L;
        for (H264VideoPacket packet : packets) {
            if (packet.kind != H264VideoPacket.KIND_VIDEO) continue;
            if (videoFrames == 0) firstPts = packet.presentationTimeUs;
            lastPts = packet.presentationTimeUs;
            videoFrames++;
        }
        return videoFrames > maxFrames || bytes > maxBytes
                || (videoFrames > 1 && lastPts - firstPts > maxSpanUs);
    }

    private void dropQueuedFrames() {
        for (H264VideoPacket packet : packets) {
            if (packet.kind == H264VideoPacket.KIND_VIDEO) droppedFrames++;
        }
        packets.clear();
        bytes = 0;
    }
}
