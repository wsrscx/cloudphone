package com.cloudphone.client;

import org.junit.Test;

import java.io.IOException;
import java.util.Arrays;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

public final class AuthSessionStoreTest {
    @Test public void payloadRoundTripsTokenAndExpiry() throws Exception {
        AuthSessionStore.CachedSession original =
                new AuthSessionStore.CachedSession("header.payload.signature", 123456789L);

        AuthSessionStore.CachedSession restored =
                AuthSessionStore.deserialize(AuthSessionStore.serialize(original));

        assertEquals(original.token, restored.token);
        assertEquals(original.expiryMs, restored.expiryMs);
    }

    @Test public void sessionRequiresFullEarlyRefreshWindow() {
        AuthSessionStore.CachedSession session =
                new AuthSessionStore.CachedSession("token", 100_000L);

        assertTrue(session.isUsable(60_000L, 30_000L));
        assertFalse(session.isUsable(70_000L, 30_000L));
        assertFalse(session.isUsable(90_000L, 30_000L));
        assertFalse(session.isUsable(Long.MAX_VALUE - 5L, 30_000L));
    }

    @Test public void payloadRejectsTrailingData() throws Exception {
        byte[] valid = AuthSessionStore.serialize(
                new AuthSessionStore.CachedSession("token", 100_000L));
        byte[] malformed = Arrays.copyOf(valid, valid.length + 1);

        try {
            AuthSessionStore.deserialize(malformed);
            fail("Expected malformed payload to be rejected");
        } catch (IOException expected) {
            // Expected.
        }
    }
}
