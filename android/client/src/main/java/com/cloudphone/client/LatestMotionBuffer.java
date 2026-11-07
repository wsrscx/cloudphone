package com.cloudphone.client;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** Single latest-value slot used ahead of the authenticated control WebSocket. */
final class LatestMotionBuffer {
    private List<ControlProtocol.Pointer> pending;

    void replace(List<ControlProtocol.Pointer> pointers) {
        pending = Collections.unmodifiableList(new ArrayList<>(pointers));
    }

    List<ControlProtocol.Pointer> peek() {
        return pending;
    }

    void complete(List<ControlProtocol.Pointer> attempted, boolean sent) {
        if (sent && pending == attempted) pending = null;
    }

    void removePointer(long pointerId) {
        if (pending == null) return;
        List<ControlProtocol.Pointer> remaining = new ArrayList<>(pending.size());
        for (ControlProtocol.Pointer pointer : pending) {
            if (pointer.id != pointerId) remaining.add(pointer);
        }
        pending = remaining.isEmpty() ? null : Collections.unmodifiableList(remaining);
    }

    void clear() {
        pending = null;
    }
}
