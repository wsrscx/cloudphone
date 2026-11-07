package com.cloudphone.client;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;

public final class H264VideoPacketTest {
    @Test public void parsesBigEndianCpv2HeaderAndPayload() {
        byte[] payload = {0, 0, 0, 1, 0x67, 1, 2, 3};
        H264VideoPacket packet = H264VideoPacket.parse(packet(
                H264VideoPacket.KIND_VIDEO, H264VideoPacket.FLAG_KEY_FRAME,
                17, 42, 900_001, 1920, 1080, payload));

        assertEquals(17L, packet.generation);
        assertEquals(42L, packet.sequence);
        assertEquals(900_001L, packet.presentationTimeUs);
        assertEquals(1920, packet.width);
        assertEquals(1080, packet.height);
        assertTrue(packet.keyFrame);
        assertArrayEquals(payload, packet.payload);
    }

    @Test(expected = IllegalArgumentException.class)
    public void rejectsDeclaredPayloadLengthMismatch() {
        byte[] value = packet(H264VideoPacket.KIND_VIDEO, 0, 1, 1, 1, 1280, 720,
                new byte[]{1, 2, 3});
        ByteBuffer.wrap(value).order(ByteOrder.BIG_ENDIAN)
                .putInt(H264VideoPacket.HEADER_SIZE - 4, 99);
        H264VideoPacket.parse(value);
    }

    static byte[] packet(int kind, int flags, int generation, long sequence, long ptsUs,
                         int width, int height, byte[] payload) {
        ByteBuffer value = ByteBuffer.allocate(H264VideoPacket.HEADER_SIZE + payload.length)
                .order(ByteOrder.BIG_ENDIAN);
        value.put(new byte[]{'C', 'P', 'V', '2'});
        value.put((byte) 2).put((byte) kind).putShort((short) flags);
        value.putInt(generation).putLong(sequence).putLong(ptsUs);
        value.putShort((short) width).putShort((short) height).putInt(payload.length);
        value.put(payload);
        return value.array();
    }
}
