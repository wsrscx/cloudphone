package com.cloudphone.client;

import android.os.SystemClock;
import android.util.Log;

import com.cloudphone.protocol.CloudPhoneProtocol.Envelope;
import com.cloudphone.protocol.CloudPhoneProtocol.Heartbeat;
import com.cloudphone.protocol.CloudPhoneProtocol.ImeFocus;
import com.cloudphone.protocol.CloudPhoneProtocol.ImeSubmit;
import com.cloudphone.protocol.CloudPhoneProtocol.NavigationCommand;
import com.cloudphone.protocol.CloudPhoneProtocol.NavigationKey;
import com.cloudphone.protocol.CloudPhoneProtocol.QualityApplied;
import com.cloudphone.protocol.CloudPhoneProtocol.QualityMode;
import com.cloudphone.protocol.CloudPhoneProtocol.QualityRequest;
import com.cloudphone.protocol.CloudPhoneProtocol.ReceiverStats;
import com.cloudphone.protocol.CloudPhoneProtocol.RotateRequest;
import com.cloudphone.protocol.CloudPhoneProtocol.SessionError;
import com.cloudphone.protocol.CloudPhoneProtocol.TouchAction;
import com.cloudphone.protocol.CloudPhoneProtocol.TouchEvent;
import com.cloudphone.protocol.CloudPhoneProtocol.TouchMoveBatch;
import com.cloudphone.protocol.CloudPhoneProtocol.TouchPoint;
import com.cloudphone.protocol.CloudPhoneProtocol.VideoGeometry;
import com.google.protobuf.InvalidProtocolBufferException;

import java.util.List;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

/** Protobuf control plane, independent from the video transport and decoder. */
public final class ControlProtocol {
    public static final int PROTOCOL_VERSION = 1;
    private static final String TAG = "CloudPhoneControl";
    static final long MOTION_RETRY_DELAY_MS = 16L;

    public interface Listener {
        void onVideoGeometry(VideoGeometry geometry, int generation);
        void onQualityApplied(QualityApplied quality);
        void onImeFocus(ImeFocus focus);
        void onSessionError(SessionError error);
    }

    interface MotionRetryScheduler {
        void schedule(Runnable task, long delayMs);
    }

    private final AtomicLong sequence = new AtomicLong();
    private final AtomicLong qualityRequestId = new AtomicLong();
    private final AtomicLong reportedFreezeCount = new AtomicLong();
    private final Listener listener;
    private final InputSendTelemetry inputTelemetry = new InputSendTelemetry();
    private final Object motionLock = new Object();
    private final LatestMotionBuffer pendingMotion = new LatestMotionBuffer();
    private final AtomicBoolean motionRetryScheduled = new AtomicBoolean();
    private final Runnable motionRetry = () -> {
        motionRetryScheduled.set(false);
        flushPendingMotion();
    };
    private volatile BinarySender sender;
    private volatile Runnable expectedVideoChangeListener;
    private volatile MotionRetryScheduler motionRetryScheduler;
    private volatile int videoGeneration;

    public ControlProtocol(Listener listener) {
        this.listener = listener;
    }

    void setExpectedVideoChangeListener(Runnable listener) {
        expectedVideoChangeListener = listener;
    }

    void setMotionRetryScheduler(MotionRetryScheduler scheduler) {
        motionRetryScheduler = scheduler;
        if (scheduler == null) motionRetryScheduled.set(false);
    }

    public void attach(BinarySender binarySender) {
        sender = binarySender;
        // A queued recovery sample may have been lost with the previous socket.
        // Re-advertising the cumulative count is idempotent on the server.
        reportedFreezeCount.set(0L);
        flushPendingMotion();
    }

    public void detach() {
        sender = null;
        synchronized (motionLock) {
            pendingMotion.clear();
        }
    }

    public boolean sendTouch(TouchAction action, Pointer pointer) {
        TouchEvent event = TouchEvent.newBuilder().setAction(action).setPoint(point(pointer)).build();
        return sendInput(base().setTouch(event), action.name().toLowerCase(Locale.US), 1);
    }

    public boolean sendMoves(List<Pointer> pointers) {
        if (pointers.isEmpty()) return true;
        synchronized (motionLock) {
            pendingMotion.replace(pointers);
        }
        return flushPendingMotion();
    }

    public void discardPendingMove(long pointerId) {
        synchronized (motionLock) {
            pendingMotion.removePointer(pointerId);
        }
    }

    public void clearPendingMoves() {
        synchronized (motionLock) {
            pendingMotion.clear();
        }
    }

    public boolean sendNavigation(NavigationKey key) {
        return sendInput(base().setNavigation(NavigationCommand.newBuilder().setKey(key)),
                key.name().toLowerCase(Locale.US), 1);
    }

    public boolean sendRotate() {
        return sendInput(base().setRotate(RotateRequest.getDefaultInstance()), "rotate", 1);
    }

    public long sendQuality(QualityMode mode) {
        long requestId = qualityRequestId.incrementAndGet();
        Envelope envelope = base().setQualityRequest(QualityRequest.newBuilder()
                .setRequestId(requestId).setMode(mode)).build();
        boolean sent = send(envelope);
        if (sent) {
            Log.i(TAG, "event=quality_requested request_id=" + requestId
                    + " mode=" + mode.name()
                    + " sent_at_elapsed_ms=" + SystemClock.elapsedRealtime()
                    + " sequence=" + envelope.getSequence()
                    + " video_generation=" + envelope.getVideoGeneration());
            notifyExpectedVideoChange();
        }
        return sent ? requestId : 0L;
    }

    public boolean sendIme(String text, int editorAction) {
        if (text.getBytes(java.nio.charset.StandardCharsets.UTF_8).length
                > Utf8ByteLengthFilter.MAX_BYTES) {
            return false;
        }
        return sendUserAction(base().setImeSubmit(ImeSubmit.newBuilder()
                .setText(text).setEditorAction(editorAction)).build(), "ime_submit", 1, false);
    }

    public boolean sendStats(ReceiverStatsSnapshot value) {
        ReceiverStats stats = ReceiverStats.newBuilder()
                .setRoundTripTimeMs(value.roundTripTimeMs)
                .setPacketLossFraction(value.packetLossFraction)
                .setJitterBufferMs(value.jitterBufferMs)
                .setFreezeCount(value.freezeCount)
                .setBytesReceived(value.bytesReceived)
                .setRenderedFps(value.renderedFps)
                .setAvailableIncomingBitrateBps(value.availableIncomingBitrateBps)
                .setNetworkJitterMs(value.networkJitterMs)
                .setFramesDropped(value.framesDropped)
                .setIncomingBitrateBps(value.incomingBitrateBps)
                .build();
        Envelope envelope = base().setReceiverStats(stats).build();
        long previousFreezeCount = reportedFreezeCount.get();
        boolean recoverySignal = value.freezeCount > previousFreezeCount;
        boolean sent = recoverySignal ? send(envelope) : sendTelemetry(envelope);
        if (sent && recoverySignal) {
            reportedFreezeCount.accumulateAndGet(value.freezeCount, Math::max);
        }
        return sent;
    }

    public boolean sendHeartbeat(long monotonicTimeMs) {
        return sendTelemetry(base().setHeartbeat(
                Heartbeat.newBuilder().setMonotonicTimeMs(monotonicTimeMs)).build());
    }

    /** Called by the control WebSocket for each authenticated binary message. */
    public void receive(byte[] data) {
        try {
            Envelope envelope = Envelope.parseFrom(data);
            if (envelope.getProtocolVersion() != PROTOCOL_VERSION) return;
            videoGeneration = envelope.getVideoGeneration();
            switch (envelope.getPayloadCase()) {
                case VIDEO_GEOMETRY:
                    listener.onVideoGeometry(envelope.getVideoGeometry(), videoGeneration);
                    break;
                case QUALITY_APPLIED:
                    QualityApplied quality = envelope.getQualityApplied();
                    Log.i(TAG, "event=quality_applied request_id=" + quality.getRequestId()
                            + " mode=" + quality.getMode().name()
                            + " width=" + quality.getWidth()
                            + " height=" + quality.getHeight()
                            + " fps=" + quality.getFps()
                            + " bitrate_bps=" + quality.getBitrateBps()
                            + " encoder_restarted=" + quality.getEncoderRestarted()
                            + " received_at_elapsed_ms=" + SystemClock.elapsedRealtime()
                            + " video_generation=" + videoGeneration);
                    listener.onQualityApplied(quality);
                    break;
                case IME_FOCUS:
                    listener.onImeFocus(envelope.getImeFocus());
                    break;
                case SESSION_ERROR:
                    listener.onSessionError(envelope.getSessionError());
                    break;
                default:
                    break;
            }
        } catch (InvalidProtocolBufferException ignored) {
            // Malformed control input is isolated to this WebSocket message.
        }
    }

    boolean flushPendingMotion() {
        synchronized (motionLock) {
            List<Pointer> pointers = pendingMotion.peek();
            BinarySender active = sender;
            if (pointers == null || !isWritable(active)) {
                return false;
            }
            if (!ControlBackpressure.canSendMotion(active.queuedBytes())) {
                scheduleMotionRetry();
                return false;
            }
            TouchMoveBatch.Builder batch = TouchMoveBatch.newBuilder();
            for (Pointer pointer : pointers) batch.addPoints(point(pointer));
            Envelope envelope = base().setTouchMoves(batch).build();
            boolean sent = active.send(envelope.toByteArray());
            pendingMotion.complete(pointers, sent);
            if (sent) {
                logInputSend(inputTelemetry.record(SystemClock.elapsedRealtime(),
                        envelope.getSequence(), envelope.getVideoGeneration(),
                        "touch_move", pointers.size(), true));
                notifyExpectedVideoChange();
            } else {
                scheduleMotionRetry();
            }
            return sent;
        }
    }

    private void scheduleMotionRetry() {
        MotionRetryScheduler scheduler = motionRetryScheduler;
        BinarySender active = sender;
        if (scheduler == null || !isWritable(active)
                || !motionRetryScheduled.compareAndSet(false, true)) {
            return;
        }
        try {
            scheduler.schedule(motionRetry, MOTION_RETRY_DELAY_MS);
        } catch (RuntimeException error) {
            motionRetryScheduled.set(false);
        }
    }

    private Envelope.Builder base() {
        return Envelope.newBuilder()
                .setProtocolVersion(PROTOCOL_VERSION)
                .setSequence(sequence.incrementAndGet())
                .setVideoGeneration(videoGeneration);
    }

    private static TouchPoint point(Pointer pointer) {
        return TouchPoint.newBuilder()
                .setPointerId(pointer.id)
                .setNormalizedX(pointer.x)
                .setNormalizedY(pointer.y)
                .setNormalizedPressure(pointer.pressure)
                .build();
    }

    private boolean sendInput(Envelope.Builder envelope, String kind, int pointCount) {
        return sendUserAction(envelope.build(), kind, pointCount, false);
    }

    private boolean sendTelemetry(Envelope envelope) {
        BinarySender active = sender;
        return isWritable(active)
                && ControlBackpressure.canSendTelemetry(active.queuedBytes())
                && active.send(envelope.toByteArray());
    }

    private boolean sendUserAction(Envelope envelope, String kind,
                                   int pointCount, boolean motion) {
        boolean sent = send(envelope);
        if (sent) {
            logInputSend(inputTelemetry.record(SystemClock.elapsedRealtime(),
                    envelope.getSequence(), envelope.getVideoGeneration(), kind, pointCount, motion));
            notifyExpectedVideoChange();
        }
        return sent;
    }

    private boolean send(Envelope envelope) {
        BinarySender active = sender;
        return isWritable(active) && active.send(envelope.toByteArray());
    }

    private static boolean isWritable(BinarySender value) {
        return value != null && value.isOpen();
    }

    private static void logInputSend(InputSendTelemetry.Record record) {
        if (record == null || !record.shouldLog) return;
        Log.d(TAG, "event=input_sent kind=" + record.kind
                + " sent_at_elapsed_ms=" + record.sentAtElapsedMs
                + " sequence=" + record.sequence
                + " video_generation=" + record.videoGeneration
                + " points=" + record.pointCount
                + " total_messages=" + record.totalMessages
                + " critical_messages=" + record.criticalMessages
                + " motion_batches=" + record.motionBatches);
    }

    private void notifyExpectedVideoChange() {
        Runnable callback = expectedVideoChangeListener;
        if (callback != null) callback.run();
    }

    public static final class Pointer {
        public final long id;
        public final int x;
        public final int y;
        public final int pressure;

        public Pointer(long id, int x, int y, int pressure) {
            this.id = id;
            this.x = x;
            this.y = y;
            this.pressure = pressure;
        }
    }
}
