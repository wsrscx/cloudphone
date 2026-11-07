package com.cloudphone.client;

import java.util.Objects;

/** Order-independent reducer for default-network available/lost callbacks. */
final class NetworkTransitionTracker<T> {
    private T current;
    private boolean replacementExpected;

    NetworkTransitionTracker(T initial) {
        current = initial;
        replacementExpected = initial == null;
    }

    boolean onAvailable(T network) {
        boolean changed = replacementExpected || (current != null && !Objects.equals(current, network));
        current = network;
        replacementExpected = false;
        return changed;
    }

    void onLost(T network) {
        if (Objects.equals(current, network)) {
            current = null;
            replacementExpected = true;
        }
    }
}
