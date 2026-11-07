package com.cloudphone.client;

/** Keeps input timing observable without logging every high-frequency motion batch. */
final class InputSendTelemetry {
    static final long DEFAULT_MOTION_LOG_INTERVAL_MS = 1_000L;

    static final class Record {
        final String kind;
        final long sentAtElapsedMs;
        final long sequence;
        final int videoGeneration;
        final int pointCount;
        final long totalMessages;
        final long criticalMessages;
        final long motionBatches;
        final boolean shouldLog;

        Record(String kind, long sentAtElapsedMs, long sequence, int videoGeneration,
               int pointCount, long totalMessages, long criticalMessages,
               long motionBatches, boolean shouldLog) {
            this.kind = kind;
            this.sentAtElapsedMs = sentAtElapsedMs;
            this.sequence = sequence;
            this.videoGeneration = videoGeneration;
            this.pointCount = pointCount;
            this.totalMessages = totalMessages;
            this.criticalMessages = criticalMessages;
            this.motionBatches = motionBatches;
            this.shouldLog = shouldLog;
        }
    }

    private final long motionLogIntervalMs;
    private long totalMessages;
    private long criticalMessages;
    private long motionBatches;
    private long lastMotionLogAtMs = -1L;

    InputSendTelemetry() {
        this(DEFAULT_MOTION_LOG_INTERVAL_MS);
    }

    InputSendTelemetry(long motionLogIntervalMs) {
        if (motionLogIntervalMs <= 0L) {
            throw new IllegalArgumentException("motion log interval must be positive");
        }
        this.motionLogIntervalMs = motionLogIntervalMs;
    }

    synchronized Record record(long sentAtElapsedMs, long sequence, int videoGeneration,
                               String kind, int pointCount, boolean motion) {
        long timestamp = Math.max(0L, sentAtElapsedMs);
        totalMessages++;
        if (motion) motionBatches++;
        else criticalMessages++;

        boolean shouldLog = !motion || lastMotionLogAtMs < 0L
                || timestamp < lastMotionLogAtMs
                || timestamp - lastMotionLogAtMs >= motionLogIntervalMs;
        if (!shouldLog) return null;
        if (motion) lastMotionLogAtMs = timestamp;
        return new Record(kind, timestamp, sequence, Math.max(0, videoGeneration),
                Math.max(0, pointCount), totalMessages, criticalMessages,
                motionBatches, shouldLog);
    }
}
