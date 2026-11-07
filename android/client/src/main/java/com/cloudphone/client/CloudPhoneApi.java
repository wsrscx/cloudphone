package com.cloudphone.client;

import androidx.annotation.Nullable;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import com.cloudphone.protocol.CloudPhoneProtocol.QualityMode;

import java.io.IOException;
import java.net.URI;
import java.security.GeneralSecurityException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.TimeUnit;

import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManager;

import okhttp3.Call;
import okhttp3.Callback;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;
import okhttp3.WebSocket;
import okhttp3.WebSocketListener;

/** Narrow client API: device identifiers are opaque and no ADB or computer-control operation exists. */
public final class CloudPhoneApi {
    private static final MediaType JSON = MediaType.get("application/json; charset=utf-8");
    private final String serverUrl;
    private final OkHttpClient client;

    public CloudPhoneApi() {
        this(BuildConfig.CLOUDPHONE_SERVER_URL, BuildConfig.CLOUDPHONE_CERTIFICATE_PIN);
    }

    CloudPhoneApi(String serverUrl, String certificatePin) {
        this.serverUrl = trimTrailingSlash(serverUrl);
        URI uri = URI.create(this.serverUrl);
        String host = uri.getHost();
        if (host == null || host.isEmpty()) {
            throw new IllegalArgumentException("CLOUDPHONE_SERVER_URL must contain a host");
        }
        PinnedCertificateTrustManager trustManager = new PinnedCertificateTrustManager(certificatePin);
        SSLContext sslContext;
        try {
            sslContext = SSLContext.getInstance("TLS");
            sslContext.init(null, new TrustManager[]{trustManager}, null);
        } catch (GeneralSecurityException error) {
            throw new IllegalStateException("Unable to initialize pinned TLS", error);
        }
        client = new OkHttpClient.Builder()
                .sslSocketFactory(sslContext.getSocketFactory(), trustManager)
                .connectTimeout(15, TimeUnit.SECONDS)
                .readTimeout(30, TimeUnit.SECONDS)
                .writeTimeout(30, TimeUnit.SECONDS)
                .pingInterval(15, TimeUnit.SECONDS)
                .build();
    }

    public void postJson(String path, JSONObject object, ApiCallback<JSONObject> callback) {
        executeJson(new Request.Builder()
                .url(url(path))
                .post(RequestBody.create(object.toString(), JSON))
                .build(), callback);
    }

    public void getDevices(String bearerToken, ApiCallback<List<DeviceInfo>> callback) {
        Request request = authenticated(url("/api/v1/devices"), bearerToken).get().build();
        executeJson(request, new ApiCallback<JSONObject>() {
            @Override public void onSuccess(JSONObject response) {
                try {
                    JSONArray array = response.optJSONArray("devices");
                    if (array == null) {
                        callback.onSuccess(Collections.emptyList());
                        return;
                    }
                    List<DeviceInfo> devices = new ArrayList<>();
                    for (int i = 0; i < array.length(); i++) {
                        devices.add(DeviceInfo.fromJson(array.getJSONObject(i)));
                    }
                    callback.onSuccess(devices);
                } catch (JSONException error) {
                    callback.onFailure(error);
                }
            }

            @Override public void onFailure(Throwable error) {
                callback.onFailure(error);
            }
        });
    }

    public void createSession(String bearerToken, String deviceId, ApiCallback<SessionInfo> callback) {
        createSession(bearerToken, deviceId, QualityMode.QUALITY_AUTO, callback);
    }

    public void createSession(String bearerToken, String deviceId, QualityMode qualityMode,
                              ApiCallback<SessionInfo> callback) {
        try {
            JSONObject body = new JSONObject()
                    .put("deviceId", deviceId)
                    .put("qualityMode", qualityMode.getNumber());
            Request request = authenticated(url("/api/v1/sessions"), bearerToken)
                    .post(RequestBody.create(body.toString(), JSON)).build();
            executeJson(request, new ApiCallback<JSONObject>() {
                @Override public void onSuccess(JSONObject response) {
                    try {
                        callback.onSuccess(parseSession(deviceId, response));
                    } catch (JSONException error) {
                        callback.onFailure(error);
                    }
                }

                @Override public void onFailure(Throwable error) {
                    callback.onFailure(error);
                }
            });
        } catch (JSONException error) {
            callback.onFailure(error);
        }
    }

    public void endSession(@Nullable String bearerToken, @Nullable String sessionId) {
        if (bearerToken == null || sessionId == null || sessionId.isEmpty()) {
            return;
        }
        Request request = authenticated(url("/api/v1/sessions/" + sessionId), bearerToken).delete().build();
        client.newCall(request).enqueue(new Callback() {
            @Override public void onFailure(Call call, IOException error) { }

            @Override public void onResponse(Call call, Response response) {
                response.close();
            }
        });
    }

    public WebSocket openBinarySocket(String socketUrl, String bearerToken,
                                      WebSocketListener listener) {
        String requestUrl = socketUrl.startsWith("/") ? serverUrl + socketUrl : socketUrl;
        if (requestUrl.startsWith("wss://")) requestUrl = "https://" + requestUrl.substring(6);
        else if (requestUrl.startsWith("ws://")) requestUrl = "http://" + requestUrl.substring(5);
        Request request = new Request.Builder().url(requestUrl)
                .header("Authorization", "Bearer " + bearerToken).build();
        return client.newWebSocket(request, listener);
    }

    private void executeJson(Request request, ApiCallback<JSONObject> callback) {
        client.newCall(request).enqueue(new Callback() {
            @Override public void onFailure(Call call, IOException error) {
                callback.onFailure(error);
            }

            @Override public void onResponse(Call call, Response response) {
                try (Response closeable = response) {
                    String body = closeable.body() == null ? "" : closeable.body().string();
                    if (!closeable.isSuccessful()) {
                        callback.onFailure(new HttpStatusException(closeable.code(), body));
                        return;
                    }
                    callback.onSuccess(new JSONObject(body));
                } catch (Throwable error) {
                    callback.onFailure(error);
                }
            }
        });
    }

    private Request.Builder authenticated(String url, String token) {
        return new Request.Builder().url(url).header("Authorization", "Bearer " + token);
    }

    private String url(String path) {
        return serverUrl + path;
    }

    private static String trimTrailingSlash(String value) {
        return value.endsWith("/") ? value.substring(0, value.length() - 1) : value;
    }

    private static SessionInfo parseSession(String deviceId, JSONObject response) throws JSONException {
        return new SessionInfo(deviceId, response.getString("sessionId"),
                requiredSocketUrl(response, "controlUrl", "control_url"),
                requiredSocketUrl(response, "videoUrl", "video_url"));
    }

    private static String requiredSocketUrl(JSONObject response, String camelCase,
                                            String snakeCase) throws JSONException {
        String value = response.optString(camelCase, "");
        if (value.isEmpty()) value = response.optString(snakeCase, "");
        if (value.isEmpty()) throw new JSONException("Missing " + camelCase);
        return value;
    }

    public static final class HttpStatusException extends IOException {
        public final int statusCode;

        HttpStatusException(int statusCode, String body) {
            super("HTTP " + statusCode + (body.isEmpty() ? "" : ": " + body));
            this.statusCode = statusCode;
        }
    }
}
