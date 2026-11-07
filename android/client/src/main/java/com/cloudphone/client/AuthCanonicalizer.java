package com.cloudphone.client;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;

/** Canonical bytes must remain byte-for-byte compatible with the Python server. */
public final class AuthCanonicalizer {
    private AuthCanonicalizer() {}

    public static byte[] canonical(String clientId, String challengeId, String nonce,
                                   long timestampMs, byte[] publicKeyDer) {
        String publicKeyHash = hex(sha256(publicKeyDer));
        String text = "v1\n" + clientId + "\n" + challengeId + "\n" + nonce + "\n"
                + timestampMs + "\n" + publicKeyHash;
        return text.getBytes(StandardCharsets.UTF_8);
    }

    public static byte[] sha256(byte[] value) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(value);
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("SHA-256 unavailable", impossible);
        }
    }

    public static String hex(byte[] value) {
        char[] digits = "0123456789abcdef".toCharArray();
        char[] chars = new char[value.length * 2];
        for (int i = 0; i < value.length; i++) {
            int unsigned = value[i] & 0xff;
            chars[i * 2] = digits[unsigned >>> 4];
            chars[i * 2 + 1] = digits[unsigned & 0x0f];
        }
        return new String(chars);
    }
}

