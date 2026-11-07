package com.cloudphone.client;

import static org.junit.Assert.assertEquals;

import java.nio.charset.StandardCharsets;

import org.junit.Test;

public final class Utf8ByteLengthFilterTest {
    @Test public void neverSplitsEmojiSurrogatePair() {
        String value = "A😀B";
        assertEquals("A", Utf8ByteLengthFilter.truncate(value, 4));
        assertEquals("A😀", Utf8ByteLengthFilter.truncate(value, 5));
    }

    @Test public void capsUtf8PayloadAtSixteenKibibytes() {
        String value = "中".repeat(6_000);
        String result = Utf8ByteLengthFilter.truncate(value, Utf8ByteLengthFilter.MAX_BYTES);
        int bytes = result.getBytes(StandardCharsets.UTF_8).length;
        assertEquals(0, bytes % 3);
        org.junit.Assert.assertTrue(bytes <= Utf8ByteLengthFilter.MAX_BYTES);
        org.junit.Assert.assertTrue(bytes + 3 > Utf8ByteLengthFilter.MAX_BYTES);
    }
}

