package com.cloudphone.client;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class ReconnectBackoffTest {
    @Test public void rapidOpenCloseUsesBoundedExponentialBackoff() {
        ReconnectBackoff backoff = new ReconnectBackoff();
        long[] expected = {1_000, 2_000, 4_000, 8_000, 8_000, 8_000, 8_000, 8_000};
        for (int index = 0; index < expected.length; index++) {
            backoff.onOpen(index * 100L);
            assertEquals(expected[index], backoff.nextDelayMs(index * 100L + 50L));
        }
    }

    @Test public void stableConnectionResetsFailures() {
        ReconnectBackoff backoff = new ReconnectBackoff();
        assertEquals(1_000, backoff.nextDelayMs(0));
        assertEquals(2_000, backoff.nextDelayMs(1));
        backoff.onOpen(10_000);
        assertEquals(1_000, backoff.nextDelayMs(20_000));
    }

    @Test public void recognizesTerminalProtocolFailures() {
        assertTrue(ReconnectBackoff.isTerminalCloseCode(4401));
        assertTrue(ReconnectBackoff.isTerminalCloseCode(4404));
        assertTrue(ReconnectBackoff.isTerminalHttpStatus(401));
        assertFalse(ReconnectBackoff.isTerminalCloseCode(1011));
        assertFalse(ReconnectBackoff.isTerminalHttpStatus(500));
    }
}
