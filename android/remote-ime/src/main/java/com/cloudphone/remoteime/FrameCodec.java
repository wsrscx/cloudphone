package com.cloudphone.remoteime;

import java.io.EOFException;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;

final class FrameCodec {
    static final int MAX_FRAME_BYTES = 128 * 1024;

    private FrameCodec() {
    }

    static byte[] readFrame(InputStream input) throws IOException {
        int first = input.read();
        if (first == -1) {
            return null;
        }
        int second = readRequiredByte(input);
        int third = readRequiredByte(input);
        int fourth = readRequiredByte(input);
        int length = (first << 24) | (second << 16) | (third << 8) | fourth;
        if (length <= 0 || length > MAX_FRAME_BYTES) {
            throw new IOException("Invalid frame length");
        }

        byte[] payload = new byte[length];
        int offset = 0;
        while (offset < length) {
            int count = input.read(payload, offset, length - offset);
            if (count == -1) {
                throw new EOFException("Truncated frame payload");
            }
            offset += count;
        }
        return payload;
    }

    static void writeFrame(OutputStream output, byte[] payload) throws IOException {
        if (payload == null || payload.length == 0 || payload.length > MAX_FRAME_BYTES) {
            throw new IOException("Invalid frame length");
        }
        int length = payload.length;
        output.write((length >>> 24) & 0xff);
        output.write((length >>> 16) & 0xff);
        output.write((length >>> 8) & 0xff);
        output.write(length & 0xff);
        output.write(payload);
        output.flush();
    }

    private static int readRequiredByte(InputStream input) throws IOException {
        int value = input.read();
        if (value == -1) {
            throw new EOFException("Truncated frame header");
        }
        return value;
    }
}
