package com.cloudphone.client;

import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import org.json.JSONObject;

import java.io.IOException;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.KeyStore;
import java.security.PrivateKey;
import java.security.Signature;
import java.security.spec.ECGenParameterSpec;
import java.util.concurrent.TimeUnit;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/** Challenge-response authentication with a non-exportable per-installation P-256 key. */
public final class AuthManager {
    private static final String KEY_ALIAS = "cloudphone.client.binding.v1";
    private static final long TOKEN_EARLY_REFRESH_MS = TimeUnit.SECONDS.toMillis(30);

    private final CloudPhoneApi api;
    private final AuthSessionStore sessionStore;
    private volatile String token;
    private volatile long tokenExpiryMs;
    private boolean authenticationInFlight;

    public AuthManager(CloudPhoneApi api, AuthSessionStore sessionStore) {
        this.api = api;
        this.sessionStore = sessionStore;
        AppSessionStore.setBearerToken(null);
        AuthSessionStore.CachedSession cached = sessionStore.load();
        if (cached != null && cached.isUsable(System.currentTimeMillis(), TOKEN_EARLY_REFRESH_MS)) {
            token = cached.token;
            tokenExpiryMs = cached.expiryMs;
            AppSessionStore.setBearerToken(token);
        } else if (cached != null) {
            sessionStore.clear();
        }
    }

    public synchronized void ensureAuthenticated(ApiCallback<String> callback) {
        if (token != null && System.currentTimeMillis() + TOKEN_EARLY_REFRESH_MS < tokenExpiryMs) {
            callback.onSuccess(token);
            return;
        }
        if (authenticationInFlight) {
            callback.onFailure(new IllegalStateException("Authentication is already in progress"));
            return;
        }
        authenticationInFlight = true;
        final JSONObject request;
        try {
            request = new JSONObject().put("clientId", BuildConfig.CLOUDPHONE_CLIENT_ID);
        } catch (org.json.JSONException error) {
            authenticationInFlight = false;
            callback.onFailure(error);
            return;
        }
        api.postJson("/api/v1/auth/challenge", request, new ApiCallback<JSONObject>() {
                    @Override public void onSuccess(JSONObject challenge) {
                        try {
                            verifyChallenge(challenge, callback);
                        } catch (Throwable error) {
                            finishFailure(callback, error);
                        }
                    }

                    @Override public void onFailure(Throwable error) {
                        finishFailure(callback, error);
                    }
        });
    }

    public synchronized void invalidate() {
        token = null;
        tokenExpiryMs = 0;
        AppSessionStore.setBearerToken(null);
        sessionStore.clear();
    }

    private void verifyChallenge(JSONObject challenge, ApiCallback<String> callback) throws Exception {
        String challengeId = challenge.getString("challengeId");
        String nonce = challenge.getString("nonce");
        // The server time in the challenge avoids rejecting otherwise valid clients with a skewed wall clock.
        long timestampMs = challenge.optLong("serverTimeMs", System.currentTimeMillis());
        KeyPair bindingKey = loadOrCreateBindingKey();
        byte[] publicKeyDer = bindingKey.getPublic().getEncoded();
        byte[] canonical = AuthCanonicalizer.canonical(BuildConfig.CLOUDPHONE_CLIENT_ID, challengeId, nonce,
                timestampMs, publicKeyDer);
        byte[] psk = Base64.decode(psk(), Base64.DEFAULT);
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(psk, "HmacSHA256"));
        byte[] hmac = mac.doFinal(canonical);
        Signature signature = Signature.getInstance("SHA256withECDSA");
        signature.initSign(bindingKey.getPrivate());
        signature.update(canonical);

        JSONObject request = new JSONObject();
        request.put("clientId", BuildConfig.CLOUDPHONE_CLIENT_ID);
        request.put("challengeId", challengeId);
        request.put("timestampMs", timestampMs);
        request.put("hmac", Base64.encodeToString(hmac, Base64.NO_WRAP));
        request.put("publicKey", Base64.encodeToString(publicKeyDer, Base64.NO_WRAP));
        request.put("keySignature", Base64.encodeToString(signature.sign(), Base64.NO_WRAP));
        api.postJson("/api/v1/auth/verify", request, new ApiCallback<JSONObject>() {
            @Override public void onSuccess(JSONObject response) {
                synchronized (AuthManager.this) {
                    token = response.optString("accessToken", null);
                    tokenExpiryMs = response.optLong("expiresAtMs", 0);
                    authenticationInFlight = false;
                    if (token == null || token.isEmpty()) {
                        callback.onFailure(new IOException("Server returned no access token"));
                        return;
                    }
                    AppSessionStore.setBearerToken(token);
                    sessionStore.save(token, tokenExpiryMs);
                }
                callback.onSuccess(token);
            }

            @Override public void onFailure(Throwable error) {
                finishFailure(callback, error);
            }
        });
    }

    private synchronized void finishFailure(ApiCallback<String> callback, Throwable error) {
        authenticationInFlight = false;
        callback.onFailure(error);
    }

    private static KeyPair loadOrCreateBindingKey() throws Exception {
        KeyStore keyStore = KeyStore.getInstance("AndroidKeyStore");
        keyStore.load(null);
        if (keyStore.containsAlias(KEY_ALIAS)) {
            PrivateKey privateKey = (PrivateKey) keyStore.getKey(KEY_ALIAS, null);
            return new KeyPair(keyStore.getCertificate(KEY_ALIAS).getPublicKey(), privateKey);
        }
        KeyPairGenerator generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore");
        KeyGenParameterSpec spec = new KeyGenParameterSpec.Builder(
                KEY_ALIAS, KeyProperties.PURPOSE_SIGN | KeyProperties.PURPOSE_VERIFY)
                .setAlgorithmParameterSpec(new ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_SHA256)
                .build();
        generator.initialize(spec);
        return generator.generateKeyPair();
    }

    private static String psk() {
        // Split fields make static extraction marginally less convenient, but do not make a hardcoded PSK secret.
        return BuildConfig.CLOUDPHONE_PSK_PART_0 + BuildConfig.CLOUDPHONE_PSK_PART_1
                + BuildConfig.CLOUDPHONE_PSK_PART_2 + BuildConfig.CLOUDPHONE_PSK_PART_3;
    }
}
