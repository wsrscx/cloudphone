package com.cloudphone.client;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import com.cloudphone.protocol.CloudPhoneProtocol.ImeFocus;
import com.cloudphone.protocol.CloudPhoneProtocol.Envelope;
import com.cloudphone.protocol.CloudPhoneProtocol.QualityApplied;
import com.cloudphone.protocol.CloudPhoneProtocol.QualityMode;
import com.cloudphone.protocol.CloudPhoneProtocol.SessionError;
import com.cloudphone.protocol.CloudPhoneProtocol.VideoGeometry;

import org.junit.Test;

import java.util.concurrent.atomic.AtomicReference;
import java.util.Collections;

public final class ControlProtocolQualityTest {
    @Test public void closedControlChannelDoesNotPretendQualityRequestWasSent() {
        ControlProtocol protocol = new ControlProtocol(noopListener());

        assertEquals(0L, protocol.sendQuality(QualityMode.QUALITY_ORIGINAL));
    }

    @Test public void attachedBinarySenderCarriesProtobufEnvelope() throws Exception {
        ControlProtocol protocol = new ControlProtocol(noopListener());
        CapturingSender sender = new CapturingSender();
        protocol.attach(sender);

        assertEquals(1L, protocol.sendQuality(QualityMode.QUALITY_1080P));
        Envelope envelope = Envelope.parseFrom(sender.lastPayload);
        assertEquals(ControlProtocol.PROTOCOL_VERSION, envelope.getProtocolVersion());
        assertEquals(QualityMode.QUALITY_1080P, envelope.getQualityRequest().getMode());
        assertEquals(1L, envelope.getQualityRequest().getRequestId());
    }

    @Test public void inboundBinaryEnvelopeIsDeliveredWithoutTransportTypes() {
        AtomicReference<QualityApplied> delivered = new AtomicReference<>();
        ControlProtocol protocol = new ControlProtocol(new ControlProtocol.Listener() {
            @Override public void onVideoGeometry(VideoGeometry geometry, int generation) {}
            @Override public void onQualityApplied(QualityApplied quality) { delivered.set(quality); }
            @Override public void onImeFocus(ImeFocus focus) {}
            @Override public void onSessionError(SessionError error) {}
        });
        QualityApplied applied = QualityApplied.newBuilder()
                .setRequestId(9).setMode(QualityMode.QUALITY_ORIGINAL).build();
        protocol.receive(Envelope.newBuilder().setProtocolVersion(ControlProtocol.PROTOCOL_VERSION)
                .setQualityApplied(applied).build().toByteArray());

        assertNotNull(delivered.get());
        assertEquals(9L, delivered.get().getRequestId());
    }

    @Test public void queuedMotionRetriesAtDisplayFrameCadence() throws Exception {
        ControlProtocol protocol = new ControlProtocol(noopListener());
        CapturingSender sender = new CapturingSender();
        sender.queuedBytes = 1L;
        CapturingScheduler scheduler = new CapturingScheduler();
        protocol.setMotionRetryScheduler(scheduler);
        protocol.attach(sender);

        ControlProtocol.Pointer point = new ControlProtocol.Pointer(1, 100, 200, 300);
        assertFalse(protocol.sendMoves(Collections.singletonList(point)));
        assertEquals(ControlProtocol.MOTION_RETRY_DELAY_MS, scheduler.delayMs);

        sender.queuedBytes = 0L;
        scheduler.task.run();
        Envelope sent = Envelope.parseFrom(sender.lastPayload);
        assertEquals(1, sent.getTouchMoves().getPointsCount());
        assertEquals(100, sent.getTouchMoves().getPoints(0).getNormalizedX());
    }

    @Test public void freezeIncrementBypassesOptionalTelemetryBackpressure() throws Exception {
        ControlProtocol protocol = new ControlProtocol(noopListener());
        CapturingSender sender = new CapturingSender();
        sender.queuedBytes = 32L;
        protocol.attach(sender);
        ReceiverStatsSnapshot recovery = new ReceiverStatsSnapshot(
                0, 0, 0, 0, 1, 100, 0, 0, 0, 0, 1920, 1080);

        assertTrue(protocol.sendStats(recovery));
        assertEquals(1L, Envelope.parseFrom(sender.lastPayload)
                .getReceiverStats().getFreezeCount());
    }

    private static ControlProtocol.Listener noopListener() {
        return new ControlProtocol.Listener() {
            @Override public void onVideoGeometry(VideoGeometry geometry, int generation) {}
            @Override public void onQualityApplied(QualityApplied quality) {}
            @Override public void onImeFocus(ImeFocus focus) {}
            @Override public void onSessionError(SessionError error) {}
        };
    }

    private static final class CapturingSender implements BinarySender {
        byte[] lastPayload;
        long queuedBytes;

        @Override public boolean isOpen() { return true; }
        @Override public long queuedBytes() { return queuedBytes; }
        @Override public boolean send(byte[] payload) {
            lastPayload = payload;
            return true;
        }
    }

    private static final class CapturingScheduler implements ControlProtocol.MotionRetryScheduler {
        Runnable task;
        long delayMs;

        @Override public void schedule(Runnable task, long delayMs) {
            this.task = task;
            this.delayMs = delayMs;
        }
    }
}
