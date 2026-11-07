package com.cloudphone.client;

import android.os.SystemClock;
import android.util.Log;
import android.view.Surface;

import androidx.annotation.Nullable;

import java.io.IOException;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

import okhttp3.Response;
import okhttp3.WebSocket;
import okhttp3.WebSocketListener;
import okio.ByteString;

/** Independent authenticated control/video WebSockets plus a direct MediaCodec sink. */
public final class DirectStreamSession implements AutoCloseable, MediaCodecVideoDecoder.Listener {
    public interface Listener {
        void onConnectionState(String state);
        void onFirstFrame();
        void onVideoFormat(int width, int height, long generation);
        void onStats(ReceiverStatsSnapshot stats);
        void onStreamError(Throwable error);
    }

    public static final class TerminalSocketException extends IOException {
        public final int statusCode;

        TerminalSocketException(int statusCode, String message) {
            super(message);
            this.statusCode = statusCode;
        }
    }

    private static final String TAG = "CloudPhoneDirect";
    private static final long VIDEO_STALL_TIMEOUT_MS = 2_500L;
    private static final long DECODER_REBUILD_COOLDOWN_MS = 5_000L;

    private final Object lock = new Object();
    private final ControlProtocol protocol;
    private final Listener listener;
    private final LatestGopQueue videoQueue = new LatestGopQueue();
    private final MediaCodecVideoDecoder decoder;
    private final ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor(runnable -> {
        Thread thread = new Thread(runnable, "CloudPhone-Direct-Monitor");
        thread.setDaemon(true);
        return thread;
    });
    private final ReconnectBackoff controlBackoff = new ReconnectBackoff();
    private final ReconnectBackoff videoBackoff = new ReconnectBackoff();
    private final AtomicLong bytesReceived = new AtomicLong();
    private final AtomicLong videoFramesReceived = new AtomicLong();
    private final AtomicLong expectedVideoSinceMs = new AtomicLong(-1L);
    private final AtomicInteger expectedVideoEvents = new AtomicInteger();
    private final AtomicLong freezeCount = new AtomicLong();
    private final AtomicLong malformedPackets = new AtomicLong();

    private CloudPhoneApi api;
    private SessionInfo session;
    private String bearerToken;
    private SocketAttempt controlAttempt;
    private SocketAttempt videoAttempt;
    private boolean closed;
    private int connectionEpoch;
    private boolean terminalReported;
    private long lastSampleMs;
    private long lastSampleBytes;
    private long lastSampleReceivedFrames;
    private long lastSampleRenderedFrames;
    private long activeWithoutRenderSinceMs = -1L;
    private long lastDecoderRebuildMs = -1L;
    private volatile int videoWidth;
    private volatile int videoHeight;
    private volatile long videoGeneration = -1L;
    private int monitorTicks;

    public DirectStreamSession(ControlProtocol protocol, Listener listener) {
        this.protocol = protocol;
        this.listener = listener;
        decoder = new MediaCodecVideoDecoder(videoQueue, this);
        protocol.setExpectedVideoChangeListener(this::onExpectedVideoChange);
        protocol.setMotionRetryScheduler((task, delayMs) -> schedule(delayMs, task));
    }

    public void start(CloudPhoneApi api, SessionInfo session, String bearerToken) {
        synchronized (lock) {
            if (this.api != null) throw new IllegalStateException("direct session already started");
            this.api = api;
            this.session = session;
            this.bearerToken = bearerToken;
            lastSampleMs = SystemClock.elapsedRealtime();
        }
        connectControl(0L);
        connectVideo(0L);
        scheduler.scheduleWithFixedDelay(this::monitorSafely, 1L, 1L, TimeUnit.SECONDS);
    }

    public void setSurface(Surface surface) {
        decoder.setSurface(surface);
    }

    public void clearSurface(Surface surface) {
        decoder.clearSurface(surface);
    }

    public void restartForNetworkChange() {
        synchronized (lock) {
            if (closed || api == null) return;
            connectionEpoch++;
            closeAttempt(controlAttempt);
            closeAttempt(videoAttempt);
            controlAttempt = null;
            videoAttempt = null;
            controlBackoff.reset();
            videoBackoff.reset();
            protocol.detach();
        }
        decoder.resetForStreamEpoch();
        listener.onConnectionState("Connecting...");
        connectControl(0L);
        connectVideo(0L);
    }

    @Override public void close() {
        synchronized (lock) {
            if (closed) return;
            closed = true;
            connectionEpoch++;
            closeAttempt(controlAttempt);
            closeAttempt(videoAttempt);
            controlAttempt = null;
            videoAttempt = null;
            protocol.detach();
            protocol.setExpectedVideoChangeListener(null);
            protocol.setMotionRetryScheduler(null);
        }
        scheduler.shutdownNow();
        decoder.close();
    }

    @Override public void onOutputFormat(int width, int height, long generation) {
        updateVideoFormat(width, height, generation);
    }

    @Override public void onFirstFrame() {
        listener.onFirstFrame();
    }

    @Override public void onDecoderFailure(Throwable error) {
        schedule(0L, () -> {
            synchronized (lock) {
                if (closed) return;
            }
            Log.w(TAG, "decoder_failed; rebuilding media decoder only", error);
            rebuildDecoder("Decoder recovering");
        });
    }

    private void connectControl(long delayMs) {
        schedule(delayMs, () -> {
            SocketAttempt attempt;
            synchronized (lock) {
                if (closed || controlAttempt != null) return;
                attempt = new SocketAttempt(true, connectionEpoch);
                controlAttempt = attempt;
            }
            WebSocket socket = api.openBinarySocket(session.controlUrl, bearerToken,
                    new DirectSocketListener(attempt));
            attempt.socket = socket;
            synchronized (lock) {
                if (closed || controlAttempt != attempt || attempt.epoch != connectionEpoch) {
                    socket.cancel();
                }
            }
        });
    }

    private void connectVideo(long delayMs) {
        schedule(delayMs, () -> {
            SocketAttempt attempt;
            synchronized (lock) {
                if (closed || videoAttempt != null) return;
                attempt = new SocketAttempt(false, connectionEpoch);
                videoAttempt = attempt;
            }
            WebSocket socket = api.openBinarySocket(session.videoUrl, bearerToken,
                    new DirectSocketListener(attempt));
            attempt.socket = socket;
            synchronized (lock) {
                if (closed || videoAttempt != attempt || attempt.epoch != connectionEpoch) {
                    socket.cancel();
                }
            }
        });
    }

    private void schedule(long delayMs, Runnable task) {
        try {
            scheduler.schedule(task, Math.max(0L, delayMs), TimeUnit.MILLISECONDS);
        } catch (java.util.concurrent.RejectedExecutionException ignored) {
            // Session closed while a reconnect callback was being delivered.
        }
    }

    private void monitor() {
        long nowMs = SystemClock.elapsedRealtime();
        long totalBytes = bytesReceived.get();
        long totalReceived = videoFramesReceived.get();
        long totalRendered = decoder.renderedFrames();
        long elapsedMs = Math.max(1L, nowMs - lastSampleMs);
        long receivedDelta = Math.max(0L, totalReceived - lastSampleReceivedFrames);
        long renderedDelta = Math.max(0L, totalRendered - lastSampleRenderedFrames);
        long bitrate = Math.max(0L, totalBytes - lastSampleBytes) * 8_000L / elapsedMs;
        double renderedFps = renderedDelta * 1_000d / elapsedMs;

        long droppedFrames = videoQueue.droppedFrames() + decoder.decodeFailures()
                + malformedPackets.get();
        ReceiverStatsSnapshot stats = new ReceiverStatsSnapshot(
                0d, 0d, 0d, 0d, freezeCount.get(), totalBytes, renderedFps,
                droppedFrames,
                bitrate, 0L, videoWidth, videoHeight);
        monitorTicks++;
        if (monitorTicks % 5 == 0) {
            Log.i(TAG, "event=direct_video_counters"
                    + " received_frames=" + totalReceived
                    + " decoded_frames=" + decoder.decodedFrames()
                    + " rendered_frames=" + totalRendered
                    + " dropped_frames=" + droppedFrames
                    + " freeze_count=" + freezeCount.get()
                    + " received_bytes=" + totalBytes
                    + " incoming_bitrate_bps=" + bitrate
                    + " queue_overloads=" + videoQueue.overloadCount()
                    + " decoder_rebuilds=" + freezeCount.get());
        }
        listener.onStats(stats);
        protocol.flushPendingMotion();
        protocol.sendStats(stats);
        if (monitorTicks % 5 == 0) protocol.sendHeartbeat(nowMs);

        boolean videoOpen;
        synchronized (lock) {
            videoOpen = videoAttempt != null && videoAttempt.open;
        }
        if (videoOpen && decoder.hasSurface() && receivedDelta > 0L && renderedDelta == 0L) {
            if (activeWithoutRenderSinceMs < 0L) activeWithoutRenderSinceMs = nowMs;
            if (nowMs - activeWithoutRenderSinceMs >= VIDEO_STALL_TIMEOUT_MS
                    && decoderRebuildCooldownElapsed(nowMs)) {
                rebuildDecoder("Video recovering");
            }
        } else if (renderedDelta > 0L) {
            activeWithoutRenderSinceMs = -1L;
            expectedVideoSinceMs.set(-1L);
            expectedVideoEvents.set(0);
            reportConnectionState();
        } else if (receivedDelta == 0L) {
            activeWithoutRenderSinceMs = -1L;
        }

        long expectedSince = expectedVideoSinceMs.get();
        if (videoOpen && decoder.hasSurface() && expectedVideoEvents.get() >= 2
                && expectedSince >= 0L && nowMs - expectedSince >= VIDEO_STALL_TIMEOUT_MS
                && decoderRebuildCooldownElapsed(nowMs)) {
            rebuildDecoder("Video recovering");
        }

        lastSampleMs = nowMs;
        lastSampleBytes = totalBytes;
        lastSampleReceivedFrames = totalReceived;
        lastSampleRenderedFrames = totalRendered;
    }

    private void monitorSafely() {
        try {
            monitor();
        } catch (Throwable error) {
            Log.e(TAG, "event=direct_monitor_failed", error);
        }
    }

    private void rebuildDecoder(String state) {
        long nowMs = SystemClock.elapsedRealtime();
        lastDecoderRebuildMs = nowMs;
        activeWithoutRenderSinceMs = -1L;
        expectedVideoSinceMs.set(-1L);
        expectedVideoEvents.set(0);
        long freezes = freezeCount.incrementAndGet();
        listener.onConnectionState(state);
        decoder.rebuildAfterStall();
        protocol.sendStats(new ReceiverStatsSnapshot(
                0d, 0d, 0d, 0d, freezes, bytesReceived.get(), 0d,
                videoQueue.droppedFrames() + decoder.decodeFailures() + malformedPackets.get(),
                0L, 0L, videoWidth, videoHeight));
    }

    private boolean decoderRebuildCooldownElapsed(long nowMs) {
        return lastDecoderRebuildMs < 0L
                || nowMs - lastDecoderRebuildMs >= DECODER_REBUILD_COOLDOWN_MS;
    }

    private void onExpectedVideoChange() {
        long nowMs = SystemClock.elapsedRealtime();
        expectedVideoSinceMs.compareAndSet(-1L, nowMs);
        expectedVideoEvents.incrementAndGet();
    }

    private void handleOpen(SocketAttempt attempt, WebSocket socket) {
        synchronized (lock) {
            if (!isCurrent(attempt)
                    || (attempt.socket != null && attempt.socket != socket)) {
                socket.cancel();
                return;
            }
            attempt.socket = socket;
            attempt.open = true;
            if (attempt.control) {
                controlBackoff.onOpen(SystemClock.elapsedRealtime());
                attempt.sender = new WebSocketBinarySender(socket);
                protocol.attach(attempt.sender);
            } else {
                videoBackoff.onOpen(SystemClock.elapsedRealtime());
            }
        }
        reportConnectionState();
    }

    private void handleBinary(SocketAttempt attempt, WebSocket socket, byte[] data) {
        synchronized (lock) {
            if (!isCurrent(attempt) || attempt.socket != socket || !attempt.open) return;
        }
        if (attempt.control) {
            protocol.receive(data);
            return;
        }
        final H264VideoPacket packet;
        try {
            packet = H264VideoPacket.parse(data);
        } catch (IllegalArgumentException error) {
            malformedPackets.incrementAndGet();
            socket.close(1003, "invalid CPV2 packet");
            return;
        }
        bytesReceived.addAndGet(data.length);
        if (packet.kind == H264VideoPacket.KIND_VIDEO) videoFramesReceived.incrementAndGet();
        updateVideoFormat(packet.width, packet.height, packet.generation);
        videoQueue.offer(packet);
    }

    private void updateVideoFormat(int width, int height, long generation) {
        if (width < 1 || height < 1) return;
        synchronized (lock) {
            if (videoWidth == width && videoHeight == height && videoGeneration == generation) return;
            videoWidth = width;
            videoHeight = height;
            videoGeneration = generation;
        }
        listener.onVideoFormat(width, height, generation);
    }

    private void handleDisconnect(SocketAttempt attempt, WebSocket socket, int closeCode,
                                  @Nullable Response response, @Nullable Throwable error) {
        long delayMs = 0L;
        boolean reconnect = false;
        boolean terminal = false;
        int statusCode = response == null ? closeCode : response.code();
        synchronized (lock) {
            if (!isCurrent(attempt) || (attempt.socket != null && attempt.socket != socket)) return;
            if (attempt.sender != null) attempt.sender.open = false;
            if (attempt.control) {
                controlAttempt = null;
                protocol.detach();
                terminal = ReconnectBackoff.isTerminalCloseCode(closeCode)
                        || ReconnectBackoff.isTerminalHttpStatus(statusCode);
                if (!closed && !terminal) {
                    delayMs = controlBackoff.nextDelayMs(SystemClock.elapsedRealtime());
                    reconnect = true;
                }
            } else {
                videoAttempt = null;
                terminal = ReconnectBackoff.isTerminalCloseCode(closeCode)
                        || ReconnectBackoff.isTerminalHttpStatus(statusCode);
                if (!closed && !terminal) {
                    delayMs = videoBackoff.nextDelayMs(SystemClock.elapsedRealtime());
                    reconnect = true;
                }
            }
        }
        reportConnectionState();
        if (terminal) {
            reportTerminal(statusCode, error == null ? "Remote session is no longer available"
                    : error.getMessage());
        } else if (reconnect) {
            if (attempt.control) connectControl(delayMs);
            else connectVideo(delayMs);
        }
    }

    private void reportTerminal(int statusCode, String message) {
        synchronized (lock) {
            if (closed || terminalReported) return;
            terminalReported = true;
        }
        listener.onStreamError(new TerminalSocketException(statusCode,
                message == null ? "WebSocket closed" : message));
    }

    private void reportConnectionState() {
        final boolean controlOpen;
        final boolean videoOpen;
        synchronized (lock) {
            if (closed) return;
            controlOpen = controlAttempt != null && controlAttempt.open;
            videoOpen = videoAttempt != null && videoAttempt.open;
        }
        if (controlOpen && videoOpen) listener.onConnectionState("Connected");
        else if (controlOpen) listener.onConnectionState("Reconnecting video...");
        else if (videoOpen) listener.onConnectionState("Reconnecting controls...");
        else listener.onConnectionState("Connecting...");
    }

    private boolean isCurrent(SocketAttempt attempt) {
        return !closed && attempt.epoch == connectionEpoch
                && (attempt.control ? controlAttempt == attempt : videoAttempt == attempt);
    }

    private static void closeAttempt(@Nullable SocketAttempt attempt) {
        if (attempt == null) return;
        if (attempt.sender != null) attempt.sender.open = false;
        if (attempt.socket != null) attempt.socket.cancel();
    }

    private static final class SocketAttempt {
        final boolean control;
        final int epoch;
        volatile WebSocket socket;
        volatile boolean open;
        WebSocketBinarySender sender;

        SocketAttempt(boolean control, int epoch) {
            this.control = control;
            this.epoch = epoch;
        }
    }

    private static final class WebSocketBinarySender implements BinarySender {
        private final WebSocket socket;
        volatile boolean open = true;

        WebSocketBinarySender(WebSocket socket) {
            this.socket = socket;
        }

        @Override public boolean isOpen() {
            return open;
        }

        @Override public long queuedBytes() {
            return socket.queueSize();
        }

        @Override public boolean send(byte[] payload) {
            return open && socket.send(ByteString.of(payload));
        }
    }

    private final class DirectSocketListener extends WebSocketListener {
        private final SocketAttempt attempt;

        DirectSocketListener(SocketAttempt attempt) {
            this.attempt = attempt;
        }

        @Override public void onOpen(WebSocket webSocket, Response response) {
            handleOpen(attempt, webSocket);
        }

        @Override public void onMessage(WebSocket webSocket, ByteString bytes) {
            handleBinary(attempt, webSocket, bytes.toByteArray());
        }

        @Override public void onMessage(WebSocket webSocket, String text) {
            webSocket.close(1003, "binary messages required");
        }

        @Override public void onClosing(WebSocket webSocket, int code, String reason) {
            webSocket.close(code, reason);
        }

        @Override public void onClosed(WebSocket webSocket, int code, String reason) {
            handleDisconnect(attempt, webSocket, code, null, null);
        }

        @Override public void onFailure(WebSocket webSocket, Throwable error,
                                        @Nullable Response response) {
            handleDisconnect(attempt, webSocket, 0, response, error);
        }
    }
}
