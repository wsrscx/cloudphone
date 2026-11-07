package com.cloudphone.client;

import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaCodecList;
import android.media.MediaFormat;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.util.Log;
import android.view.Surface;

import java.nio.ByteBuffer;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

/** Dedicated-thread H.264 decoder which can be rebuilt without touching either WebSocket. */
final class MediaCodecVideoDecoder implements AutoCloseable {
    interface Listener {
        void onOutputFormat(int width, int height, long generation);
        void onFirstFrame();
        void onDecoderFailure(Throwable error);
    }

    private static final String TAG = "CloudPhoneDecoder";
    private static final String MIME_TYPE = MediaFormat.MIMETYPE_VIDEO_AVC;
    private static final int MAX_INPUT_WAIT_ATTEMPTS = 8;
    private static final long INPUT_WAIT_SLICE_US = 25_000L;

    private final LatestGopQueue queue;
    private final Listener listener;
    private final HandlerThread frameCallbackThread = new HandlerThread(
            "CloudPhone-FrameRendered");
    private final Handler frameCallbackHandler;
    private final List<DecoderSelector.Candidate> candidates;
    private final AtomicLong decodedFrames = new AtomicLong();
    private final AtomicLong renderedFrames = new AtomicLong();
    private final AtomicLong decodeFailures = new AtomicLong();
    private final AtomicBoolean firstFrameReported = new AtomicBoolean();
    private Surface surface;
    private Worker worker;
    private boolean closed;
    private int workerSerial;
    private int preferredCandidateOffset;
    private volatile int consecutiveStalls;

    MediaCodecVideoDecoder(LatestGopQueue queue, Listener listener) {
        this.queue = queue;
        this.listener = listener;
        frameCallbackThread.start();
        frameCallbackHandler = new Handler(frameCallbackThread.getLooper());
        boolean preferSoftware = DecoderSelector.shouldPreferSoftware(Build.VERSION.SDK_INT,
                Build.MANUFACTURER, Build.BRAND, Build.MODEL, Build.FINGERPRINT);
        candidates = DecoderSelector.ordered(discoverAvcDecoders(), preferSoftware);
        Log.i(TAG, "prefer_software=" + preferSoftware + " candidates=" + codecNames(candidates));
    }

    synchronized void setSurface(Surface value) {
        if (closed || surface == value) return;
        surface = value;
        restartWorker(false);
    }

    synchronized void clearSurface(Surface value) {
        if (surface != value) return;
        surface = null;
        stopWorker();
        queue.resetForDecoderRecovery();
    }

    synchronized void rebuildAfterStall() {
        if (closed) return;
        consecutiveStalls++;
        if (consecutiveStalls % 2 == 0 && candidates.size() > 1) {
            preferredCandidateOffset = (preferredCandidateOffset + 1) % candidates.size();
        }
        restartWorker(true);
    }

    synchronized void resetForStreamEpoch() {
        consecutiveStalls = 0;
        preferredCandidateOffset = 0;
        restartWorker(true);
    }

    long decodedFrames() {
        return decodedFrames.get();
    }

    long renderedFrames() {
        return renderedFrames.get();
    }

    long decodeFailures() {
        return decodeFailures.get();
    }

    boolean hasSurface() {
        synchronized (this) {
            return surface != null && surface.isValid();
        }
    }

    @Override public synchronized void close() {
        if (closed) return;
        closed = true;
        stopWorker();
        queue.close();
        surface = null;
        frameCallbackThread.quitSafely();
    }

    private void restartWorker(boolean clearQueue) {
        stopWorker();
        if (clearQueue) queue.resetForDecoderRecovery();
        if (surface == null || !surface.isValid() || closed) return;
        Worker replacement = new Worker(++workerSerial, surface, preferredCandidateOffset);
        worker = replacement;
        replacement.start();
    }

    private void stopWorker() {
        Worker previous = worker;
        worker = null;
        workerSerial++;
        if (previous != null) previous.shutdown();
    }

    private synchronized boolean isCurrent(Worker value) {
        return !closed && worker == value && value.serial == workerSerial;
    }

    private void reportFailure(Worker source, Throwable error) {
        if (!isCurrent(source)) return;
        decodeFailures.incrementAndGet();
        listener.onDecoderFailure(error);
    }

    private final class Worker extends Thread {
        final int serial;
        private final Surface outputSurface;
        private final int candidateOffset;
        private volatile boolean running = true;
        private MediaCodec codec;
        private long generation = -1L;
        private long lastPtsUs = -1L;

        Worker(int serial, Surface outputSurface, int candidateOffset) {
            super("CloudPhone-H264-" + serial);
            this.serial = serial;
            this.outputSurface = outputSurface;
            this.candidateOffset = candidateOffset;
        }

        @Override public void run() {
            try {
                while (running && isCurrent(this)) {
                    H264VideoPacket packet = queue.poll(15L, TimeUnit.MILLISECONDS);
                    if (packet == null) {
                        drainOutput(0L);
                        continue;
                    }
                    if (packet.isReset()) {
                        releaseCodec();
                        generation = packet.generation;
                        lastPtsUs = -1L;
                        continue;
                    }
                    if (packet.generation != generation) {
                        releaseCodec();
                        generation = packet.generation;
                        lastPtsUs = -1L;
                    }
                    if (codec == null) {
                        if (!packet.keyFrame) continue;
                        configure(packet);
                    }
                    queueInput(packet);
                    drainOutput(2_000L);
                }
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            } catch (Throwable error) {
                reportFailure(this, error);
            } finally {
                releaseCodec();
            }
        }

        void shutdown() {
            running = false;
            interrupt();
        }

        private void configure(H264VideoPacket keyFrame) throws Exception {
            AnnexBParameterSets parameterSets = AnnexBParameterSets.extract(keyFrame.payload);
            Throwable lastError = null;
            int attempts = Math.max(1, candidates.size());
            for (int attempt = 0; attempt < attempts; attempt++) {
                MediaCodec candidate = null;
                String name = candidates.isEmpty() ? null
                        : candidates.get((candidateOffset + attempt) % candidates.size()).name;
                try {
                    candidate = name == null ? MediaCodec.createDecoderByType(MIME_TYPE)
                            : MediaCodec.createByCodecName(name);
                    MediaFormat format = MediaFormat.createVideoFormat(
                            MIME_TYPE, keyFrame.width, keyFrame.height);
                    format.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE,
                            Math.max(keyFrame.payload.length, LatestGopQueue.DEFAULT_MAX_BYTES));
                    format.setInteger(MediaFormat.KEY_PRIORITY, 0);
                    if (parameterSets.sps != null) {
                        format.setByteBuffer("csd-0", ByteBuffer.wrap(parameterSets.sps));
                    }
                    if (parameterSets.pps != null) {
                        format.setByteBuffer("csd-1", ByteBuffer.wrap(parameterSets.pps));
                    }
                    candidate.configure(format, outputSurface, null, 0);
                    MediaCodec configured = candidate;
                    configured.setOnFrameRenderedListener((ignored, ptsUs, nanoTime) -> {
                        if (!isCurrent(Worker.this) || codec != configured) return;
                        renderedFrames.incrementAndGet();
                        consecutiveStalls = 0;
                        if (firstFrameReported.compareAndSet(false, true)) listener.onFirstFrame();
                    }, frameCallbackHandler);
                    candidate.start();
                    codec = candidate;
                    Log.i(TAG, "decoder_started name=" + candidate.getName()
                            + " width=" + keyFrame.width + " height=" + keyFrame.height
                            + " generation=" + keyFrame.generation);
                    listener.onOutputFormat(keyFrame.width, keyFrame.height, keyFrame.generation);
                    return;
                } catch (Throwable error) {
                    lastError = error;
                    if (candidate != null) {
                        try { candidate.release(); } catch (Throwable ignored) { }
                    }
                    Log.w(TAG, "decoder_configure_failed name=" + name, error);
                }
            }
            if (lastError instanceof Exception) throw (Exception) lastError;
            throw new IllegalStateException("No AVC decoder could be configured", lastError);
        }

        private void queueInput(H264VideoPacket packet) {
            int inputIndex = -1;
            for (int attempt = 0; attempt < MAX_INPUT_WAIT_ATTEMPTS && inputIndex < 0; attempt++) {
                drainOutput(0L);
                inputIndex = codec.dequeueInputBuffer(INPUT_WAIT_SLICE_US);
            }
            if (inputIndex < 0) throw new IllegalStateException("AVC decoder input stalled");
            ByteBuffer input = codec.getInputBuffer(inputIndex);
            if (input == null || input.capacity() < packet.payload.length) {
                throw new IllegalStateException("AVC access unit exceeds decoder input buffer");
            }
            input.clear();
            input.put(packet.payload);
            long ptsUs = Math.max(lastPtsUs + 1L, Math.max(0L, packet.presentationTimeUs));
            lastPtsUs = ptsUs;
            int flags = packet.keyFrame ? MediaCodec.BUFFER_FLAG_KEY_FRAME : 0;
            codec.queueInputBuffer(inputIndex, 0, packet.payload.length, ptsUs, flags);
        }

        private void drainOutput(long timeoutUs) {
            if (codec == null) return;
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            long timeout = timeoutUs;
            while (true) {
                int outputIndex = codec.dequeueOutputBuffer(info, timeout);
                timeout = 0L;
                if (outputIndex >= 0) {
                    boolean render = info.size > 0;
                    codec.releaseOutputBuffer(outputIndex, render);
                    if (render) decodedFrames.incrementAndGet();
                    continue;
                }
                if (outputIndex == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    MediaFormat output = codec.getOutputFormat();
                    int width = output.containsKey(MediaFormat.KEY_WIDTH)
                            ? output.getInteger(MediaFormat.KEY_WIDTH) : 0;
                    int height = output.containsKey(MediaFormat.KEY_HEIGHT)
                            ? output.getInteger(MediaFormat.KEY_HEIGHT) : 0;
                    if (width > 0 && height > 0) listener.onOutputFormat(width, height, generation);
                    continue;
                }
                return;
            }
        }

        private void releaseCodec() {
            MediaCodec previous = codec;
            codec = null;
            if (previous == null) return;
            try { previous.stop(); } catch (Throwable ignored) { }
            try { previous.release(); } catch (Throwable ignored) { }
        }
    }

    private static List<DecoderSelector.Candidate> discoverAvcDecoders() {
        List<DecoderSelector.Candidate> result = new ArrayList<>();
        MediaCodecInfo[] infos;
        try {
            infos = new MediaCodecList(MediaCodecList.ALL_CODECS).getCodecInfos();
        } catch (Throwable error) {
            Log.w(TAG, "Unable to enumerate AVC decoders", error);
            return result;
        }
        for (MediaCodecInfo info : infos) {
            if (info.isEncoder() || !supportsAvc(info)) continue;
            boolean software = Build.VERSION.SDK_INT >= 29
                    ? info.isSoftwareOnly() : isSoftwareName(info.getName());
            boolean hardware = Build.VERSION.SDK_INT >= 29
                    ? info.isHardwareAccelerated() : !software;
            result.add(new DecoderSelector.Candidate(info.getName(), software, hardware));
        }
        return result;
    }

    private static boolean supportsAvc(MediaCodecInfo info) {
        for (String type : info.getSupportedTypes()) {
            if (MIME_TYPE.equalsIgnoreCase(type)) return true;
        }
        return false;
    }

    private static boolean isSoftwareName(String name) {
        String lower = name.toLowerCase(java.util.Locale.US);
        return lower.startsWith("omx.google.") || lower.startsWith("c2.android.")
                || lower.contains("software") || lower.contains("sw.decoder");
    }

    private static String codecNames(List<DecoderSelector.Candidate> values) {
        StringBuilder result = new StringBuilder();
        for (DecoderSelector.Candidate value : values) {
            if (result.length() > 0) result.append(',');
            result.append(value.name);
        }
        return result.toString();
    }
}
