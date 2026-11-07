package com.cloudphone.client;

import android.content.Intent;
import android.graphics.Color;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;

import com.google.android.material.button.MaterialButton;

import java.util.ArrayList;
import java.util.List;

public final class MainActivity extends AppCompatActivity implements DeviceAdapter.Listener {
    private static final int MAX_TRANSIENT_SESSION_RETRIES = 2;
    private static final long TRANSIENT_SESSION_RETRY_DELAY_MS = 750L;

    private final Handler retryHandler = new Handler(Looper.getMainLooper());
    private CloudPhoneApi api;
    private AuthManager auth;
    private DeviceAdapter adapter;
    private TextView statusView;
    private MaterialButton refreshButton;
    private boolean creatingSession;

    @Override protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        api = new CloudPhoneApi();
        auth = new AuthManager(api, new AuthSessionStore(this));
        buildContent();
    }

    @Override protected void onResume() {
        super.onResume();
        creatingSession = false;
        refreshDevices();
    }

    @Override protected void onDestroy() {
        retryHandler.removeCallbacksAndMessages(null);
        super.onDestroy();
    }

    private void buildContent() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(getColor(R.color.cloud_background));
        root.setPadding(dp(18), dp(12), dp(18), 0);

        LinearLayout header = new LinearLayout(this);
        header.setGravity(Gravity.CENTER_VERTICAL);
        TextView title = new TextView(this);
        title.setText(R.string.devices);
        title.setTextColor(getColor(R.color.cloud_text));
        title.setTextSize(24);
        title.setTypeface(title.getTypeface(), android.graphics.Typeface.BOLD);
        header.addView(title, new LinearLayout.LayoutParams(0, dp(64), 1));
        refreshButton = new MaterialButton(this);
        refreshButton.setText(R.string.refresh);
        refreshButton.setOnClickListener(view -> refreshDevices());
        header.addView(refreshButton, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(48)));
        root.addView(header, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(72)));

        statusView = new TextView(this);
        statusView.setTextColor(getColor(R.color.cloud_muted));
        statusView.setTextSize(15);
        statusView.setGravity(Gravity.CENTER);
        root.addView(statusView, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(44)));

        RecyclerView list = new RecyclerView(this);
        list.setLayoutManager(new LinearLayoutManager(this));
        list.setClipToPadding(false);
        list.setPadding(0, 0, 0, dp(24));
        adapter = new DeviceAdapter(this);
        list.setAdapter(adapter);
        root.addView(list, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));
        setContentView(root);
    }

    private void refreshDevices() {
        if (creatingSession) {
            return;
        }
        refreshButton.setEnabled(false);
        statusView.setText(R.string.connecting);
        loadDevices(false);
    }

    private void loadDevices(boolean retriedAfterUnauthorized) {
        auth.ensureAuthenticated(new ApiCallback<String>() {
            @Override public void onSuccess(String token) {
                api.getDevices(token, new ApiCallback<List<DeviceInfo>>() {
                    @Override public void onSuccess(List<DeviceInfo> devices) {
                        List<DeviceInfo> authorized = new ArrayList<>();
                        for (DeviceInfo device : devices) {
                            if (device.authorized) {
                                authorized.add(device);
                            }
                        }
                        runOnUiThread(() -> {
                            adapter.submit(authorized);
                            if (authorized.isEmpty()) statusView.setText(R.string.not_authorized);
                            else statusView.setText("");
                            refreshButton.setEnabled(true);
                        });
                    }

                    @Override public void onFailure(Throwable error) {
                        if (!retriedAfterUnauthorized && isUnauthorized(error)) {
                            auth.invalidate();
                            loadDevices(true);
                            return;
                        }
                        showError(error);
                    }
                });
            }

            @Override public void onFailure(Throwable error) {
                showError(error);
            }
        });
    }

    @Override public void onDeviceClicked(DeviceInfo device) {
        if (creatingSession || !device.isConnectable()) {
            return;
        }
        creatingSession = true;
        statusView.setText(R.string.connecting);
        adapter.setEnabled(false);
        createSession(device, false, 0);
    }

    private void createSession(DeviceInfo device, boolean retriedAfterUnauthorized,
                               int transientRetryCount) {
        String token = AppSessionStore.getBearerToken();
        if (token == null) {
            auth.ensureAuthenticated(new ApiCallback<String>() {
                @Override public void onSuccess(String ignored) {
                    createSession(device, retriedAfterUnauthorized, transientRetryCount);
                }

                @Override public void onFailure(Throwable error) {
                    finishCreateSessionFailure(error);
                }
            });
            return;
        }
        api.createSession(token, device.id, QualityPreference.load(this), new ApiCallback<SessionInfo>() {
            @Override public void onSuccess(SessionInfo session) {
                AppSessionStore.setActiveSession(session);
                runOnUiThread(() -> {
                    startActivity(new Intent(MainActivity.this, ControlActivity.class));
                    adapter.setEnabled(true);
                });
            }

            @Override public void onFailure(Throwable error) {
                if (!retriedAfterUnauthorized && isUnauthorized(error)) {
                    auth.invalidate();
                    createSession(device, true, transientRetryCount);
                    return;
                }
                if (isServiceUnavailable(error)
                        && transientRetryCount < MAX_TRANSIENT_SESSION_RETRIES) {
                    long delayMs = TRANSIENT_SESSION_RETRY_DELAY_MS * (transientRetryCount + 1L);
                    retryHandler.postDelayed(() -> {
                        if (creatingSession) {
                            createSession(device, retriedAfterUnauthorized, transientRetryCount + 1);
                        }
                    }, delayMs);
                    return;
                }
                finishCreateSessionFailure(error);
            }
        });
    }

    private void finishCreateSessionFailure(Throwable error) {
        creatingSession = false;
        runOnUiThread(() -> adapter.setEnabled(true));
        showError(error);
    }

    private static boolean isUnauthorized(Throwable error) {
        return error instanceof CloudPhoneApi.HttpStatusException
                && ((CloudPhoneApi.HttpStatusException) error).statusCode == 401;
    }

    private static boolean isServiceUnavailable(Throwable error) {
        return error instanceof CloudPhoneApi.HttpStatusException
                && ((CloudPhoneApi.HttpStatusException) error).statusCode == 503;
    }

    private void showError(Throwable error) {
        runOnUiThread(() -> {
            refreshButton.setEnabled(true);
            statusView.setText(R.string.connection_error);
            Toast.makeText(this, error.getMessage(), Toast.LENGTH_LONG).show();
        });
    }

    int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
