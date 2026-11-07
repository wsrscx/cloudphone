package com.cloudphone.client;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.Network;

/** Reports actual default-network changes, suppressing the initial callback. */
public final class NetworkChangeMonitor implements AutoCloseable {
    public interface Listener {
        void onNetworkChanged();
    }

    private final ConnectivityManager connectivityManager;
    private final Listener listener;
    private NetworkTransitionTracker<Network> transitions;
    private boolean registered;

    private final ConnectivityManager.NetworkCallback callback = new ConnectivityManager.NetworkCallback() {
        @Override public void onAvailable(Network network) {
            NetworkTransitionTracker<Network> tracker = transitions;
            if (tracker == null) {
                transitions = new NetworkTransitionTracker<>(network);
            } else if (tracker.onAvailable(network)) {
                listener.onNetworkChanged();
            }
        }

        @Override public void onLost(Network network) {
            NetworkTransitionTracker<Network> tracker = transitions;
            if (tracker != null) tracker.onLost(network);
        }
    };

    public NetworkChangeMonitor(Context context, Listener listener) {
        connectivityManager = (ConnectivityManager) context.getSystemService(Context.CONNECTIVITY_SERVICE);
        this.listener = listener;
    }

    public void start() {
        if (registered) return;
        transitions = new NetworkTransitionTracker<>(connectivityManager.getActiveNetwork());
        connectivityManager.registerDefaultNetworkCallback(callback);
        registered = true;
    }

    @Override public void close() {
        if (!registered) return;
        connectivityManager.unregisterNetworkCallback(callback);
        transitions = null;
        registered = false;
    }
}
