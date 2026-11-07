package com.cloudphone.remoteime;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.CharBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.Base64;

final class ImeWireProtocol {
    static final int VERSION = 1;
    static final int MAX_TEXT_BYTES = 16 * 1024;

    static final int TYPE_COMMIT_TEXT = 0x20;
    static final int TYPE_EDITOR_ACTION = 0x21;
    static final int TYPE_PING = 0x30;

    private ImeWireProtocol() {
    }

    static boolean isValidAuth(byte[] payload, byte[] expectedToken) {
        if (payload == null) {
            return false;
        }
        byte[] suppliedToken = null;
        try {
            JSONObject message = parseObject(payload);
            if (!"auth".equals(message.optString("type"))) {
                return false;
            }
            suppliedToken = Base64.getUrlDecoder().decode(message.getString("token"));
            return MessageDigest.isEqual(suppliedToken, expectedToken);
        } catch (IOException | JSONException | IllegalArgumentException exception) {
            return false;
        } finally {
            if (suppliedToken != null) {
                Arrays.fill(suppliedToken, (byte) 0);
            }
        }
    }

    static Command decodeCommand(byte[] payload) throws IOException {
        try {
            JSONObject message = parseObject(payload);
            String type = message.getString("type");
            if ("commit".equals(type)) {
                String text = message.getString("text");
                if (text.getBytes(StandardCharsets.UTF_8).length > MAX_TEXT_BYTES) {
                    throw new IOException("Text command is too large");
                }
                return Command.commit(text, message.optInt("editorAction", 0));
            }
            if ("ping".equals(type)) {
                return Command.ping();
            }
            throw new IOException("Unknown command type");
        } catch (JSONException exception) {
            throw new IOException("Invalid command", exception);
        }
    }

    static byte[] authResult(boolean accepted) {
        JSONObject message = new JSONObject();
        put(message, "type", "auth_result");
        put(message, "version", VERSION);
        put(message, "accepted", accepted);
        return json(message);
    }

    static byte[] inputStarted(EditorSnapshot snapshot) {
        return focusEvent(true, snapshot);
    }

    static byte[] inputFinished(EditorSnapshot snapshot) {
        return focusEvent(false, snapshot);
    }

    static byte[] commandResult(boolean success) {
        JSONObject message = new JSONObject();
        put(message, "type", "command_result");
        put(message, "success", success);
        return json(message);
    }

    static byte[] pong() {
        JSONObject message = new JSONObject();
        put(message, "type", "pong");
        return json(message);
    }

    private static byte[] focusEvent(boolean active, EditorSnapshot snapshot) {
        JSONObject message = new JSONObject();
        put(message, "type", "focus");
        put(message, "active", active);
        put(message, "inputType", snapshot.inputType);
        put(message, "imeOptions", snapshot.imeOptions);
        put(message, "password", snapshot.password);
        put(message, "restarting", snapshot.restarting);
        return json(message);
    }

    private static JSONObject parseObject(byte[] payload) throws IOException, JSONException {
        CharBuffer decoded;
        try {
            decoded = StandardCharsets.UTF_8.newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(ByteBuffer.wrap(payload));
        } catch (CharacterCodingException exception) {
            throw new IOException("Malformed UTF-8", exception);
        }
        return new JSONObject(decoded.toString());
    }

    private static byte[] json(JSONObject object) {
        return object.toString().getBytes(StandardCharsets.UTF_8);
    }

    private static void put(JSONObject object, String key, Object value) {
        try {
            object.put(key, value);
        } catch (JSONException impossible) {
            throw new AssertionError(impossible);
        }
    }

    static final class Command {
        final int type;
        final String text;
        final int editorAction;

        private Command(int type, String text, int editorAction) {
            this.type = type;
            this.text = text;
            this.editorAction = editorAction;
        }

        static Command commit(String text, int editorAction) {
            return new Command(TYPE_COMMIT_TEXT, text, editorAction);
        }

        static Command ping() {
            return new Command(TYPE_PING, null, 0);
        }
    }
}
