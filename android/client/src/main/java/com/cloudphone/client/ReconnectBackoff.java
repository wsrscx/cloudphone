package com.cloudphone.client;

/** Retry state which only resets after a connection has remained open for a useful interval. */
final class ReconnectBackoff {
    static final long STABLE_OPEN_MS = 10_000L;
    private static final int MAX_BACKOFF_EXPONENT = 3;

    private int attempts;
    private long openedAtMs = -1L;

    void onOpen(long nowMs) {
        openedAtMs = nowMs;
    }

    long nextDelayMs(long nowMs) {
        if (openedAtMs >= 0L && nowMs - openedAtMs >= STABLE_OPEN_MS) attempts = 0;
        openedAtMs = -1L;
        long delay = 1_000L << Math.min(MAX_BACKOFF_EXPONENT, attempts);
        if (attempts < MAX_BACKOFF_EXPONENT) attempts++;
        return delay;
    }

    void reset() {
        attempts = 0;
        openedAtMs = -1L;
    }

    static boolean isTerminalCloseCode(int code) {
        return (code >= 4400 && code < 4500) || code == 1002 || code == 1003 || code == 1008;
    }

    static boolean isTerminalHttpStatus(int statusCode) {
        return statusCode == 401 || statusCode == 403 || statusCode == 404;
    }
}
