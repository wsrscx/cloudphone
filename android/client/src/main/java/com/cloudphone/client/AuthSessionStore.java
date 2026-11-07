package com.cloudphone.client;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Log;

import androidx.annotation.Nullable;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.KeyStore;
import java.util.Base64;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** Persists the short-lived bearer session encrypted by a non-exportable Android Keystore key. */
public final class AuthSessionStore {
    private static final String TAG = "AuthSessionStore";
    private static final String KEY_ALIAS = "cloudphone.client.auth_session.v1";
    private static final String PREFERENCES = "cloudphone.auth_session.v1";
    private static final String ENCRYPTED_SESSION = "encrypted_session";
    private static final byte[] AAD = "cloudphone:auth-session:v1".getBytes(StandardCharsets.UTF_8);
    private static final int FORMAT_VERSION = 1;
    private static final int GCM_TAG_BITS = 128;

    private final SharedPreferences preferences;

    public AuthSessionStore(Context context) {
        preferences = context.getApplicationContext()
                .getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE);
    }

    @Nullable public CachedSession load() {
        String encoded = preferences.getString(ENCRYPTED_SESSION, null);
        if (encoded == null || encoded.isEmpty()) return null;
        try {
            byte[] envelope = Base64.getDecoder().decode(encoded);
            DataInputStream input = new DataInputStream(new ByteArrayInputStream(envelope));
            int version = input.readUnsignedByte();
            if (version != FORMAT_VERSION) throw new IOException("Unsupported auth session format");
            int ivLength = input.readUnsignedByte();
            if (ivLength < 12 || ivLength > 32 || input.available() <= ivLength) {
                throw new IOException("Invalid auth session envelope");
            }
            byte[] iv = new byte[ivLength];
            input.readFully(iv);
            byte[] ciphertext = new byte[input.available()];
            input.readFully(ciphertext);

            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, loadOrCreateKey(), new GCMParameterSpec(GCM_TAG_BITS, iv));
            cipher.updateAAD(AAD);
            return deserialize(cipher.doFinal(ciphertext));
        } catch (GeneralSecurityException | IOException | RuntimeException error) {
            Log.w(TAG, "Discarding unreadable cached authentication session", error);
            clear();
            deleteKeySilently();
            return null;
        }
    }

    public void save(String token, long expiryMs) {
        if (token == null || token.isEmpty() || expiryMs <= 0) {
            clear();
            return;
        }
        try {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, loadOrCreateKey());
            cipher.updateAAD(AAD);
            byte[] ciphertext = cipher.doFinal(serialize(new CachedSession(token, expiryMs)));
            byte[] iv = cipher.getIV();

            ByteArrayOutputStream bytes = new ByteArrayOutputStream(2 + iv.length + ciphertext.length);
            DataOutputStream output = new DataOutputStream(bytes);
            output.writeByte(FORMAT_VERSION);
            output.writeByte(iv.length);
            output.write(iv);
            output.write(ciphertext);
            output.flush();
            boolean saved = preferences.edit().putString(ENCRYPTED_SESSION,
                    Base64.getEncoder().encodeToString(bytes.toByteArray())).commit();
            if (!saved) Log.w(TAG, "Unable to commit encrypted authentication session");
        } catch (GeneralSecurityException | IOException | RuntimeException error) {
            Log.w(TAG, "Unable to cache authentication session", error);
            clear();
            deleteKeySilently();
        }
    }

    public void clear() {
        if (!preferences.edit().remove(ENCRYPTED_SESSION).commit()) {
            Log.w(TAG, "Unable to clear encrypted authentication session");
        }
    }

    static byte[] serialize(CachedSession session) throws IOException {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        DataOutputStream output = new DataOutputStream(bytes);
        output.writeUTF(session.token);
        output.writeLong(session.expiryMs);
        output.flush();
        return bytes.toByteArray();
    }

    static CachedSession deserialize(byte[] plaintext) throws IOException {
        DataInputStream input = new DataInputStream(new ByteArrayInputStream(plaintext));
        String token = input.readUTF();
        long expiryMs = input.readLong();
        if (token.isEmpty() || expiryMs <= 0 || input.available() != 0) {
            throw new IOException("Invalid auth session payload");
        }
        return new CachedSession(token, expiryMs);
    }

    private static SecretKey loadOrCreateKey() throws GeneralSecurityException, IOException {
        KeyStore keyStore = KeyStore.getInstance("AndroidKeyStore");
        keyStore.load(null);
        if (keyStore.containsAlias(KEY_ALIAS)) {
            return (SecretKey) keyStore.getKey(KEY_ALIAS, null);
        }
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,
                "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build());
        return generator.generateKey();
    }

    private static void deleteKeySilently() {
        try {
            KeyStore keyStore = KeyStore.getInstance("AndroidKeyStore");
            keyStore.load(null);
            if (keyStore.containsAlias(KEY_ALIAS)) keyStore.deleteEntry(KEY_ALIAS);
        } catch (GeneralSecurityException | IOException | RuntimeException error) {
            Log.w(TAG, "Unable to reset authentication session key", error);
        }
    }

    static final class CachedSession {
        final String token;
        final long expiryMs;

        CachedSession(String token, long expiryMs) {
            this.token = token;
            this.expiryMs = expiryMs;
        }

        boolean isUsable(long nowMs, long earlyRefreshMs) {
            return expiryMs > nowMs && expiryMs - nowMs > earlyRefreshMs;
        }
    }
}
