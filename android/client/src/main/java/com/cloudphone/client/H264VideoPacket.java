package com.cloudphone.client;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.Arrays;

/** Immutable CPV2 H.264 Annex-B access unit received from the video WebSocket. */
public final class H264VideoPacket {
    public static final int HEADER_SIZE = 36;
    public static final int KIND_VIDEO = 1;
    public static final int KIND_RESET = 2;
    public static final int FLAG_KEY_FRAME = 1;
    public static final int MAX_PAYLOAD_SIZE = 16 * 1024 * 1024;
    private static final int MAGIC = 0x43505632; // CPV2
    private static final int VERSION = 2;

    public final int kind;
    public final long generation;
    public final long sequence;
    public final long presentationTimeUs;
    public final int width;
    public final int height;
    public final boolean keyFrame;
    public final byte[] payload;

    H264VideoPacket(int kind, long generation, long sequence, long presentationTimeUs,
                    int width, int height, boolean keyFrame, byte[] payload) {
        this.kind = kind;
        this.generation = generation;
        this.sequence = sequence;
        this.presentationTimeUs = presentationTimeUs;
        this.width = width;
        this.height = height;
        this.keyFrame = keyFrame;
        this.payload = payload;
    }

    public static H264VideoPacket parse(byte[] data) {
        if (data.length < HEADER_SIZE) throw new IllegalArgumentException("truncated CPV2 packet");
        ByteBuffer header = ByteBuffer.wrap(data).order(ByteOrder.BIG_ENDIAN);
        if (header.getInt() != MAGIC || (header.get() & 0xff) != VERSION) {
            throw new IllegalArgumentException("unsupported CPV2 packet");
        }
        int kind = header.get() & 0xff;
        int flags = header.getShort() & 0xffff;
        long generation = header.getInt() & 0xffff_ffffL;
        long sequence = header.getLong();
        long ptsUs = header.getLong();
        int width = header.getShort() & 0xffff;
        int height = header.getShort() & 0xffff;
        long payloadSize = header.getInt() & 0xffff_ffffL;
        if ((kind != KIND_VIDEO && kind != KIND_RESET) || payloadSize > MAX_PAYLOAD_SIZE) {
            throw new IllegalArgumentException("invalid CPV2 header");
        }
        if (payloadSize != data.length - HEADER_SIZE) {
            throw new IllegalArgumentException("CPV2 payload size mismatch");
        }
        if (width < 1 || height < 1) throw new IllegalArgumentException("invalid video geometry");
        if (kind == KIND_VIDEO && payloadSize == 0L) {
            throw new IllegalArgumentException("empty H.264 access unit");
        }
        return new H264VideoPacket(kind, generation, sequence, ptsUs, width, height,
                (flags & FLAG_KEY_FRAME) != 0, Arrays.copyOfRange(data, HEADER_SIZE, data.length));
    }

    public boolean isReset() {
        return kind == KIND_RESET;
    }
}
