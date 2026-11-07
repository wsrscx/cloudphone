package com.cloudphone.client;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class NetworkTransitionTrackerTest {
    @Test public void detectsLostThenAvailableHandover() {
        NetworkTransitionTracker<String> tracker = new NetworkTransitionTracker<>("wifi");
        tracker.onLost("wifi");
        assertTrue(tracker.onAvailable("cell"));
    }

    @Test public void detectsAvailableThenLostHandoverOnlyOnce() {
        NetworkTransitionTracker<String> tracker = new NetworkTransitionTracker<>("wifi");
        assertTrue(tracker.onAvailable("cell"));
        tracker.onLost("wifi");
        assertFalse(tracker.onAvailable("cell"));
    }

    @Test public void initialOfflineThenAvailableIsARealTransition() {
        NetworkTransitionTracker<String> tracker = new NetworkTransitionTracker<>(null);
        assertTrue(tracker.onAvailable("wifi"));
        assertFalse(tracker.onAvailable("wifi"));
    }
}
