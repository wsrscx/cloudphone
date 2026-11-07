package com.cloudphone.client;

import android.text.InputFilter;
import android.text.Spanned;

import java.nio.charset.StandardCharsets;

/** Enforces the remote IME's 16 KiB UTF-8 message boundary without splitting a code point. */
public final class Utf8ByteLengthFilter implements InputFilter {
    public static final int MAX_BYTES = 16 * 1024;

    @Override public CharSequence filter(CharSequence source, int start, int end, Spanned destination,
                                         int destinationStart, int destinationEnd) {
        String replaced = destination.subSequence(destinationStart, destinationEnd).toString();
        int retainedBytes = utf8Length(destination.toString()) - utf8Length(replaced);
        int remaining = Math.max(0, MAX_BYTES - retainedBytes);
        String incoming = source.subSequence(start, end).toString();
        if (utf8Length(incoming) <= remaining) return null;
        return truncate(incoming, remaining);
    }

    static String truncate(String value, int maximumBytes) {
        if (maximumBytes <= 0) return "";
        int end = 0;
        int used = 0;
        while (end < value.length()) {
            int codePoint = value.codePointAt(end);
            int charCount = Character.charCount(codePoint);
            int bytes = utf8Length(new String(Character.toChars(codePoint)));
            if (used + bytes > maximumBytes) break;
            used += bytes;
            end += charCount;
        }
        return value.substring(0, end);
    }

    private static int utf8Length(String value) {
        return value.getBytes(StandardCharsets.UTF_8).length;
    }
}

