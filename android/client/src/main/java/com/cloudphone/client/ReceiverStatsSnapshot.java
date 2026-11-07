package com.cloudphone.client;

public final class ReceiverStatsSnapshot {
    public final double roundTripTimeMs;
    public final double packetLossFraction;
    public final double networkJitterMs;
    public final double jitterBufferMs;
    public final long freezeCount;
    public final long bytesReceived;
    public final long incomingBitrateBps;
    public final double renderedFps;
    public final long framesDropped;
    public final long availableIncomingBitrateBps;
    public final int frameWidth;
    public final int frameHeight;

    public ReceiverStatsSnapshot(double roundTripTimeMs, double packetLossFraction, double networkJitterMs,
                                 double jitterBufferMs, long freezeCount, long bytesReceived, double renderedFps,
                                 long framesDropped, long incomingBitrateBps,
                                 long availableIncomingBitrateBps, int frameWidth, int frameHeight) {
        this.roundTripTimeMs = finiteNonNegative(roundTripTimeMs);
        this.packetLossFraction = Math.max(0d, Math.min(1d, packetLossFraction));
        this.networkJitterMs = finiteNonNegative(networkJitterMs);
        this.jitterBufferMs = finiteNonNegative(jitterBufferMs);
        this.freezeCount = Math.max(0, freezeCount);
        this.bytesReceived = Math.max(0, bytesReceived);
        this.incomingBitrateBps = Math.max(0, incomingBitrateBps);
        this.renderedFps = finiteNonNegative(renderedFps);
        this.framesDropped = Math.max(0, framesDropped);
        this.availableIncomingBitrateBps = Math.max(0, availableIncomingBitrateBps);
        this.frameWidth = Math.max(0, frameWidth);
        this.frameHeight = Math.max(0, frameHeight);
    }

    private static double finiteNonNegative(double value) {
        return Double.isFinite(value) ? Math.max(0d, value) : 0d;
    }
}
