package com.cloudphone.remoteime;

import org.junit.Test;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.EOFException;
import java.io.IOException;
import java.util.Arrays;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.fail;

public class FrameCodecTest {
    @Test
    public void roundTripsOneFrame() throws Exception {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] expected = new byte[]{1, 2, 3, 4};

        FrameCodec.writeFrame(output, expected);

        assertArrayEquals(expected, FrameCodec.readFrame(new ByteArrayInputStream(output.toByteArray())));
    }

    @Test
    public void cleanEndOfStreamReturnsNull() throws Exception {
        assertNull(FrameCodec.readFrame(new ByteArrayInputStream(new byte[0])));
    }

    @Test
    public void rejectsTruncatedHeader() throws Exception {
        expectEof(new byte[]{0, 0});
    }

    @Test
    public void rejectsTruncatedPayload() throws Exception {
        expectEof(new byte[]{0, 0, 0, 3, 1, 2});
    }

    @Test
    public void rejectsOversizedFrame() throws Exception {
        int length = FrameCodec.MAX_FRAME_BYTES + 1;
        byte[] header = new byte[]{
                (byte) (length >>> 24), (byte) (length >>> 16), (byte) (length >>> 8), (byte) length
        };
        try {
            FrameCodec.readFrame(new ByteArrayInputStream(header));
            fail("Expected IOException");
        } catch (IOException expected) {
            // Expected.
        }
    }

    private static void expectEof(byte[] bytes) throws Exception {
        try {
            FrameCodec.readFrame(new ByteArrayInputStream(Arrays.copyOf(bytes, bytes.length)));
            fail("Expected EOFException");
        } catch (EOFException expected) {
            // Expected.
        }
    }
}
