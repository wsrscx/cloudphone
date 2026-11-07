package com.cloudphone.client;

import static org.junit.Assert.assertEquals;

import java.nio.charset.StandardCharsets;

import org.junit.Test;

public final class AuthCanonicalizerTest {
    @Test public void canonicalFormMatchesServerContract() {
        String actual = new String(AuthCanonicalizer.canonical(
                "client", "challenge", "nonce", 123L, new byte[0]), StandardCharsets.UTF_8);
        assertEquals("v1\nclient\nchallenge\nnonce\n123\n"
                + "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", actual);
    }
}

