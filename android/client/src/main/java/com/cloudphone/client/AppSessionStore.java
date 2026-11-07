package com.cloudphone.client;

/**
 * Keeps bearer credentials and active sessions out of intents. Persistent credential storage is
 * handled separately by {@link AuthSessionStore} using Android Keystore encryption.
 */
public final class AppSessionStore {
    private static volatile String bearerToken;
    private static volatile SessionInfo activeSession;

    private AppSessionStore() {}

    public static void setBearerToken(String value) {
        bearerToken = value;
    }

    public static String getBearerToken() {
        return bearerToken;
    }

    public static void setActiveSession(SessionInfo value) {
        activeSession = value;
    }

    public static SessionInfo takeActiveSession() {
        return activeSession;
    }

    public static void clearActiveSession() {
        activeSession = null;
    }
}
