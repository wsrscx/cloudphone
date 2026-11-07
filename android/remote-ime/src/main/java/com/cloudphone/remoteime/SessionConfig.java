package com.cloudphone.remoteime;

import android.content.Context;
import android.content.SharedPreferences;

import java.util.Base64;
import java.util.regex.Pattern;

final class SessionConfig {
    private static final String PREFERENCES_NAME = "remote_ime_session";
    private static final String KEY_SOCKET_NAME = "socket_name";
    private static final String KEY_TOKEN = "session_token";
    private static final Pattern SOCKET_NAME_PATTERN =
            Pattern.compile("^[A-Za-z0-9][A-Za-z0-9._-]{15,95}$");
    private static final int TOKEN_BYTES = 32;

    private final String socketName;
    private final byte[] token;

    private SessionConfig(String socketName, byte[] token) {
        this.socketName = socketName;
        this.token = token.clone();
    }

    static SessionConfig parse(String socketName, String encodedToken) {
        if (socketName == null || !SOCKET_NAME_PATTERN.matcher(socketName).matches()) {
            throw new IllegalArgumentException("Invalid socket name");
        }
        if (encodedToken == null) {
            throw new IllegalArgumentException("Missing session token");
        }

        final byte[] decoded;
        try {
            decoded = Base64.getUrlDecoder().decode(encodedToken);
        } catch (IllegalArgumentException exception) {
            throw new IllegalArgumentException("Invalid session token", exception);
        }
        if (decoded.length != TOKEN_BYTES) {
            throw new IllegalArgumentException("Invalid session token length");
        }
        return new SessionConfig(socketName, decoded);
    }

    static SessionConfig load(Context context) {
        SharedPreferences preferences = context.getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE);
        String socketName = preferences.getString(KEY_SOCKET_NAME, null);
        String encodedToken = preferences.getString(KEY_TOKEN, null);
        if (socketName == null || encodedToken == null) {
            return null;
        }
        try {
            return parse(socketName, encodedToken);
        } catch (IllegalArgumentException exception) {
            preferences.edit().clear().apply();
            return null;
        }
    }

    static void save(Context context, SessionConfig config) {
        String encodedToken = Base64.getUrlEncoder().withoutPadding().encodeToString(config.token);
        context.getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE)
                .edit()
                .putString(KEY_SOCKET_NAME, config.socketName)
                .putString(KEY_TOKEN, encodedToken)
                .apply();
    }

    static void clear(Context context) {
        context.getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE).edit().clear().apply();
    }

    String socketName() {
        return socketName;
    }

    byte[] token() {
        return token.clone();
    }
}
