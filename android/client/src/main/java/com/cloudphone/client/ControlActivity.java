package com.cloudphone.client;

import android.content.Context;
import android.content.Intent;
import android.content.pm.ActivityInfo;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.text.InputFilter;
import android.text.InputType;
import android.text.method.PasswordTransformationMethod;
import android.view.Gravity;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputMethodManager;
import android.widget.ArrayAdapter;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.activity.OnBackPressedCallback;
import androidx.appcompat.app.AppCompatActivity;

import com.cloudphone.protocol.CloudPhoneProtocol.ImeFocus;
import com.cloudphone.protocol.CloudPhoneProtocol.NavigationKey;
import com.cloudphone.protocol.CloudPhoneProtocol.QualityApplied;
import com.cloudphone.protocol.CloudPhoneProtocol.QualityMode;
import com.cloudphone.protocol.CloudPhoneProtocol.SessionError;
import com.cloudphone.protocol.CloudPhoneProtocol.VideoGeometry;
import com.google.android.material.button.MaterialButton;

import java.util.Locale;

public final class ControlActivity extends AppCompatActivity
        implements ControlProtocol.Listener, DirectStreamSession.Listener {
    private static final int LANDSCAPE_TOOLBAR_DP = 40;
    private static final int PORTRAIT_TOOLBAR_DP = 64;

    private CloudPhoneApi api;
    private SessionInfo session;
    private String bearerToken;
    private ControlProtocol protocol;
    private VideoCoordinateMapper coordinateMapper;
    private MultiTouchController touchController;
    private DirectStreamSession directStream;
    private NetworkChangeMonitor networkMonitor;

    private FrameLayout root;
    private SurfaceView renderer;
    private LinearLayout toolbar;
    private LinearLayout operationPanel;
    private LinearLayout inputBar;
    private EditText input;
    private TextView metrics;
    private TextView connectionState;
    private Spinner quality;
    private ReceiverStatsSnapshot latestStats;
    private QualityApplied latestQuality;
    private QualityMode confirmedQualityMode = QualityMode.QUALITY_AUTO;
    private long pendingQualityRequestId;
    private boolean ending;
    private boolean recoveringSession;
    private boolean hasRenderedFrame;
    private int remoteVideoWidth = 16;
    private int remoteVideoHeight = 9;

    @Override protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        session = AppSessionStore.takeActiveSession();
        bearerToken = AppSessionStore.getBearerToken();
        if (session == null || bearerToken == null) {
            finish();
            return;
        }
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
                | WindowManager.LayoutParams.FLAG_FULLSCREEN);
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                | View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_LAYOUT_STABLE);
        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override public void handleOnBackPressed() {
                handleLocalBack();
            }
        });

        api = new CloudPhoneApi();
        coordinateMapper = new VideoCoordinateMapper();
        protocol = new ControlProtocol(this);
        directStream = new DirectStreamSession(protocol, this);
        confirmedQualityMode = QualityPreference.load(this);
        buildContent();
        touchController = new MultiTouchController(protocol, coordinateMapper);
        renderer.setOnTouchListener(touchController);
        directStream.start(api, session, bearerToken);
        networkMonitor = new NetworkChangeMonitor(this, () -> runOnUiThread(() -> {
            touchController.cancelAll();
            connectionState.setText(R.string.connecting);
            directStream.restartForNetworkChange();
        }));
        networkMonitor.start();
    }

    @Override public void onConfigurationChanged(@NonNull Configuration newConfig) {
        super.onConfigurationChanged(newConfig);
        layoutOverlays();
    }

    private void handleLocalBack() {
        if (operationPanel != null && operationPanel.getVisibility() == View.VISIBLE) {
            operationPanel.setVisibility(View.GONE);
        } else {
            protocol.sendNavigation(NavigationKey.NAVIGATION_BACK);
        }
    }

    @Override protected void onDestroy() {
        endSession(false);
        super.onDestroy();
    }

    private void buildContent() {
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);
        root.addOnLayoutChangeListener((view, left, top, right, bottom,
                                        oldLeft, oldTop, oldRight, oldBottom) -> {
            if (right - left != oldRight - oldLeft || bottom - top != oldBottom - oldTop) {
                layoutVideoSurface();
            }
        });
        renderer = new SurfaceView(this);
        renderer.getHolder().addCallback(new SurfaceHolder.Callback() {
            @Override public void surfaceCreated(@NonNull SurfaceHolder holder) {
                directStream.setSurface(holder.getSurface());
            }

            @Override public void surfaceChanged(@NonNull SurfaceHolder holder, int format,
                                                 int width, int height) { }

            @Override public void surfaceDestroyed(@NonNull SurfaceHolder holder) {
                directStream.clearSurface(holder.getSurface());
            }
        });
        root.addView(renderer, new FrameLayout.LayoutParams(0, 0, Gravity.TOP | Gravity.START));

        connectionState = new TextView(this);
        connectionState.setText(R.string.connecting);
        connectionState.setTextColor(getColor(R.color.cloud_text));
        connectionState.setTextSize(13);
        connectionState.setPadding(dp(10), dp(6), dp(10), dp(6));
        connectionState.setBackground(panelBackground(0xCC0B1422, 6));
        FrameLayout.LayoutParams stateParams = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.TOP | Gravity.START);
        stateParams.setMargins(dp(12), dp(12), dp(12), dp(12));
        root.addView(connectionState, stateParams);

        toolbar = new LinearLayout(this);
        toolbar.setBackgroundColor(0xEB05080D);
        toolbar.setPadding(dp(4), dp(4), dp(4), dp(4));
        toolbar.addView(toolbarButton(R.string.operation, R.drawable.ic_controls,
                view -> toggleOperations()), weighted());
        toolbar.addView(toolbarButton(R.string.back, R.drawable.ic_back,
                view -> protocol.sendNavigation(NavigationKey.NAVIGATION_BACK)), weighted());
        toolbar.addView(toolbarButton(R.string.home, R.drawable.ic_home,
                view -> protocol.sendNavigation(NavigationKey.NAVIGATION_HOME)), weighted());
        toolbar.addView(toolbarButton(R.string.recents, R.drawable.ic_recents,
                view -> protocol.sendNavigation(NavigationKey.NAVIGATION_RECENTS)), weighted());
        root.addView(toolbar);

        operationPanel = createOperationPanel();
        operationPanel.setVisibility(View.GONE);
        root.addView(operationPanel);

        inputBar = createInputBar();
        inputBar.setVisibility(View.GONE);
        root.addView(inputBar);
        setContentView(root);
        layoutOverlays();
    }

    private LinearLayout createOperationPanel() {
        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setPadding(dp(18), dp(20), dp(18), dp(20));
        panel.setBackgroundColor(0xF21A202B);

        TextView heading = new TextView(this);
        heading.setText(R.string.operation);
        heading.setTextColor(getColor(R.color.cloud_text));
        heading.setTextSize(20);
        heading.setTypeface(Typeface.DEFAULT_BOLD);
        panel.addView(heading, matchWrap(dp(48)));

        LinearLayout commands = new LinearLayout(this);
        commands.setOrientation(LinearLayout.HORIZONTAL);
        commands.addView(panelButton(R.string.exit, R.drawable.ic_exit, view -> endSession(true)), weighted());
        commands.addView(panelButton(R.string.rotate, R.drawable.ic_rotate,
                view -> protocol.sendRotate()), weighted());
        panel.addView(commands, matchWrap(dp(72)));

        TextView qualityLabel = new TextView(this);
        qualityLabel.setText(R.string.quality);
        qualityLabel.setTextColor(getColor(R.color.cloud_muted));
        qualityLabel.setTextSize(14);
        panel.addView(qualityLabel, matchWrap(dp(36)));

        quality = new Spinner(this);
        ArrayAdapter<CharSequence> qualities = ArrayAdapter.createFromResource(this,
                R.array.quality_modes, android.R.layout.simple_spinner_item);
        qualities.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        quality.setAdapter(qualities);
        quality.setSelection(QualityPreference.spinnerPosition(confirmedQualityMode), false);
        quality.setOnItemSelectedListener(new android.widget.AdapterView.OnItemSelectedListener() {
            @Override public void onItemSelected(android.widget.AdapterView<?> parent, View view,
                                                 int position, long id) {
                QualityMode selected = QualityPreference.fromSpinnerPosition(position);
                if (pendingQualityRequestId == 0L && selected == confirmedQualityMode) return;
                if (pendingQualityRequestId != 0L) {
                    showConfirmedQuality();
                    return;
                }
                long requestId = protocol.sendQuality(selected);
                if (requestId == 0L) {
                    showConfirmedQuality();
                    Toast.makeText(ControlActivity.this, R.string.quality_not_ready, Toast.LENGTH_SHORT).show();
                    return;
                }
                pendingQualityRequestId = requestId;
                quality.setEnabled(false);
            }
            @Override public void onNothingSelected(android.widget.AdapterView<?> parent) { }
        });
        panel.addView(quality, matchWrap(dp(52)));

        metrics = new TextView(this);
        metrics.setText(R.string.metrics_unknown);
        metrics.setTextColor(getColor(R.color.cloud_muted));
        metrics.setTextSize(14);
        metrics.setPadding(0, dp(18), 0, 0);
        panel.addView(metrics, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return panel;
    }

    private LinearLayout createInputBar() {
        LinearLayout bar = new LinearLayout(this);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setPadding(dp(10), dp(8), dp(10), dp(8));
        bar.setBackground(panelBackground(0xF21A202B, 6));
        input = new EditText(this);
        input.setSingleLine(false);
        input.setMaxLines(3);
        input.setHint(R.string.input_hint);
        input.setHintTextColor(getColor(R.color.cloud_muted));
        input.setTextColor(getColor(R.color.cloud_text));
        input.setBackgroundColor(Color.TRANSPARENT);
        input.setFilters(new InputFilter[]{new Utf8ByteLengthFilter()});
        input.setOnEditorActionListener((view, actionId, event) -> {
            submitIme(actionId);
            return true;
        });
        bar.addView(input, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        MaterialButton send = new MaterialButton(this);
        send.setText(R.string.send);
        send.setOnClickListener(view -> submitIme(input.getImeOptions() & EditorInfo.IME_MASK_ACTION));
        bar.addView(send, new LinearLayout.LayoutParams(dp(88), dp(52)));
        return bar;
    }

    private void layoutOverlays() {
        if (toolbar == null) return;
        boolean landscape = getResources().getConfiguration().orientation == Configuration.ORIENTATION_LANDSCAPE;
        toolbar.setOrientation(landscape ? LinearLayout.VERTICAL : LinearLayout.HORIZONTAL);
        for (int index = 0; index < toolbar.getChildCount(); index++) {
            View child = toolbar.getChildAt(index);
            if (child instanceof MaterialButton) {
                MaterialButton button = (MaterialButton) child;
                int textResource = (Integer) button.getTag();
                button.setText(landscape ? "" : getString(textResource));
                button.setIconPadding(landscape ? 0 : dp(4));
            }
            child.setLayoutParams(landscape
                    ? new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1)
                    : new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1));
        }
        FrameLayout.LayoutParams toolbarParams = new FrameLayout.LayoutParams(
                landscape ? dp(LANDSCAPE_TOOLBAR_DP) : ViewGroup.LayoutParams.MATCH_PARENT,
                landscape ? ViewGroup.LayoutParams.MATCH_PARENT : dp(PORTRAIT_TOOLBAR_DP),
                landscape ? Gravity.END : Gravity.BOTTOM);
        toolbar.setLayoutParams(toolbarParams);

        root.post(this::layoutVideoSurface);

        FrameLayout.LayoutParams panelParams = new FrameLayout.LayoutParams(
                landscape ? dp(340) : ViewGroup.LayoutParams.MATCH_PARENT,
                landscape ? ViewGroup.LayoutParams.MATCH_PARENT : dp(410),
                landscape ? Gravity.END : Gravity.BOTTOM);
        if (landscape) panelParams.rightMargin = dp(LANDSCAPE_TOOLBAR_DP);
        else panelParams.bottomMargin = dp(PORTRAIT_TOOLBAR_DP);
        operationPanel.setLayoutParams(panelParams);

        FrameLayout.LayoutParams inputParams = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM);
        if (landscape) inputParams.rightMargin = dp(LANDSCAPE_TOOLBAR_DP);
        else inputParams.bottomMargin = dp(PORTRAIT_TOOLBAR_DP);
        inputParams.leftMargin = dp(8);
        inputParams.topMargin = dp(8);
        inputBar.setLayoutParams(inputParams);
        connectionState.bringToFront();
        toolbar.bringToFront();
        inputBar.bringToFront();
        operationPanel.bringToFront();
    }

    private void layoutVideoSurface() {
        if (root == null || renderer == null || root.getWidth() == 0 || root.getHeight() == 0) return;
        boolean landscape = getResources().getConfiguration().orientation == Configuration.ORIENTATION_LANDSCAPE;
        int availableWidth = Math.max(0, root.getWidth() - (landscape ? dp(LANDSCAPE_TOOLBAR_DP) : 0));
        int availableHeight = Math.max(0, root.getHeight() - (landscape ? 0 : dp(PORTRAIT_TOOLBAR_DP)));
        AspectFitLayout.Bounds bounds = AspectFitLayout.fit(
                availableWidth, availableHeight, remoteVideoWidth, remoteVideoHeight);
        FrameLayout.LayoutParams params = new FrameLayout.LayoutParams(
                bounds.width, bounds.height, Gravity.TOP | Gravity.START);
        params.leftMargin = bounds.left;
        params.topMargin = bounds.top;
        renderer.setLayoutParams(params);
    }

    private MaterialButton toolbarButton(int text, int icon, View.OnClickListener listener) {
        MaterialButton button = new MaterialButton(this, null,
                com.google.android.material.R.attr.materialButtonOutlinedStyle);
        button.setText(text);
        button.setTag(text);
        button.setContentDescription(getString(text));
        button.setTooltipText(getString(text));
        button.setTextSize(12);
        button.setTextColor(getColor(R.color.cloud_text));
        button.setIconResource(icon);
        button.setIconTintResource(R.color.cloud_text);
        button.setIconGravity(MaterialButton.ICON_GRAVITY_TEXT_TOP);
        button.setIconPadding(dp(4));
        button.setGravity(Gravity.CENTER);
        button.setPadding(dp(4), dp(4), dp(4), dp(4));
        button.setCornerRadius(dp(6));
        button.setInsetTop(0);
        button.setInsetBottom(0);
        button.setOnClickListener(listener);
        return button;
    }

    private MaterialButton panelButton(int text, int icon, View.OnClickListener listener) {
        MaterialButton button = toolbarButton(text, icon, listener);
        LinearLayout.LayoutParams params = weighted();
        params.setMargins(dp(3), dp(3), dp(3), dp(3));
        button.setLayoutParams(params);
        return button;
    }

    private void toggleOperations() {
        boolean showing = operationPanel.getVisibility() == View.VISIBLE;
        operationPanel.setVisibility(showing ? View.GONE : View.VISIBLE);
        if (!showing) operationPanel.bringToFront();
    }

    private void submitIme(int editorAction) {
        String value = input.getText().toString();
        if (!protocol.sendIme(value, editorAction)) return;
        input.setText("");
    }

    private void updateIme(ImeFocus focus) {
        runOnUiThread(() -> {
            InputMethodManager keyboard = (InputMethodManager) getSystemService(Context.INPUT_METHOD_SERVICE);
            if (!focus.getActive()) {
                keyboard.hideSoftInputFromWindow(input.getWindowToken(), 0);
                input.clearFocus();
                inputBar.setVisibility(View.GONE);
                return;
            }
            int inputType = focus.getInputType() == 0 ? InputType.TYPE_CLASS_TEXT : focus.getInputType();
            input.setInputType(inputType);
            input.setImeOptions(focus.getImeOptions());
            input.setTransformationMethod(focus.getPassword() ? PasswordTransformationMethod.getInstance() : null);
            inputBar.setVisibility(View.VISIBLE);
            inputBar.bringToFront();
            input.requestFocus();
            input.setSelection(input.length());
            input.postDelayed(() -> keyboard.showSoftInput(input, InputMethodManager.SHOW_IMPLICIT), 100);
        });
    }

    private void updateMetrics() {
        if (metrics == null) return;
        String targetResolution = latestQuality == null
                ? "--" : latestQuality.getWidth() + "x" + latestQuality.getHeight();
        String actualResolution = latestStats == null || latestStats.frameWidth == 0 || latestStats.frameHeight == 0
                ? "--" : latestStats.frameWidth + "x" + latestStats.frameHeight;
        String resolution = actualResolution + " recv / " + targetResolution + " target";
        double targetBitrateMbps = latestQuality == null ? 0d : latestQuality.getBitrateBps() / 1_000_000d;
        double incomingBitrateMbps = latestStats == null ? 0d : latestStats.incomingBitrateBps / 1_000_000d;
        double rtt = latestStats == null ? 0d : latestStats.roundTripTimeMs;
        double loss = latestStats == null ? 0d : latestStats.packetLossFraction * 100d;
        double fps = latestStats == null ? 0d : latestStats.renderedFps;
        double networkJitter = latestStats == null ? 0d : latestStats.networkJitterMs;
        double bufferDelay = latestStats == null ? 0d : latestStats.jitterBufferMs;
        long dropped = latestStats == null ? 0L : latestStats.framesDropped;
        metrics.setText(String.format(Locale.getDefault(),
                "%s | recv %.1f / target %.1f Mbps | %.0f fps\nRTT %.0f ms | jitter %.1f ms | buffer %.0f ms\n%.1f%% loss | %d dropped/s",
                resolution, incomingBitrateMbps, targetBitrateMbps, fps,
                rtt, networkJitter, bufferDelay, loss, dropped));
    }

    private void endSession(boolean finishActivity) {
        if (ending) return;
        ending = true;
        if (touchController != null) touchController.cancelAll();
        if (networkMonitor != null) networkMonitor.close();
        if (directStream != null) directStream.close();
        hideKeyboard();
        if (api != null && session != null) api.endSession(bearerToken, session.sessionId);
        AppSessionStore.clearActiveSession();
        if (finishActivity && !isFinishing()) finish();
    }

    private void recoverSession() {
        if (ending || recoveringSession || api == null || session == null || bearerToken == null) return;
        recoveringSession = true;
        if (touchController != null) touchController.cancelAll();
        connectionState.setVisibility(View.VISIBLE);
        connectionState.setText(R.string.connecting);
        api.createSession(bearerToken, session.deviceId, confirmedQualityMode,
                new ApiCallback<SessionInfo>() {
                    @Override public void onSuccess(SessionInfo replacement) {
                        runOnUiThread(() -> handoffToReplacementSession(replacement));
                    }

                    @Override public void onFailure(Throwable error) {
                        runOnUiThread(() -> {
                            recoveringSession = false;
                            Toast.makeText(ControlActivity.this, error.getMessage(), Toast.LENGTH_LONG).show();
                            endSession(true);
                        });
                    }
                });
    }

    private void handoffToReplacementSession(SessionInfo replacement) {
        if (ending) {
            api.endSession(bearerToken, replacement.sessionId);
            return;
        }
        String previousSessionId = session.sessionId;
        ending = true;
        if (touchController != null) touchController.cancelAll();
        if (networkMonitor != null) networkMonitor.close();
        if (directStream != null) directStream.close();
        hideKeyboard();
        if (!previousSessionId.equals(replacement.sessionId)) {
            api.endSession(bearerToken, previousSessionId);
        }
        AppSessionStore.setActiveSession(replacement);
        startActivity(new Intent(this, ControlActivity.class));
        finish();
    }

    private void hideKeyboard() {
        if (input == null) return;
        InputMethodManager keyboard = (InputMethodManager) getSystemService(Context.INPUT_METHOD_SERVICE);
        keyboard.hideSoftInputFromWindow(input.getWindowToken(), 0);
    }

    @Override public void onVideoGeometry(VideoGeometry geometry, int generation) {
        updateVideoGeometry(geometry.getWidth(), geometry.getHeight());
    }

    @Override public void onVideoFormat(int width, int height, long generation) {
        updateVideoGeometry(width, height);
    }

    private void updateVideoGeometry(int width, int height) {
        runOnUiThread(() -> {
            touchController.cancelAll();
            coordinateMapper.updateVideoSize(width, height);
            remoteVideoWidth = Math.max(1, width);
            remoteVideoHeight = Math.max(1, height);
            layoutVideoSurface();
            boolean remoteLandscape = width >= height;
            boolean localLandscape = getResources().getConfiguration().orientation
                    == Configuration.ORIENTATION_LANDSCAPE;
            if (remoteLandscape != localLandscape) {
                setRequestedOrientation(remoteLandscape ? ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
                        : ActivityInfo.SCREEN_ORIENTATION_SENSOR_PORTRAIT);
            }
        });
    }

    @Override public void onQualityApplied(QualityApplied applied) {
        runOnUiThread(() -> {
            if (applied.getEncoderRestarted()) touchController.cancelAll();
            latestQuality = applied;
            if (pendingQualityRequestId == 0L || applied.getRequestId() == pendingQualityRequestId) {
                QualityMode appliedMode = applied.getMode();
                if (appliedMode != QualityMode.QUALITY_MODE_UNSPECIFIED
                        && appliedMode != QualityMode.UNRECOGNIZED) {
                    confirmedQualityMode = appliedMode;
                    QualityPreference.save(this, confirmedQualityMode);
                }
                pendingQualityRequestId = 0L;
                quality.setEnabled(true);
                showConfirmedQuality();
            }
            updateMetrics();
        });
    }

    @Override public void onImeFocus(ImeFocus focus) {
        updateIme(focus);
    }

    @Override public void onSessionError(SessionError error) {
        runOnUiThread(() -> {
            if (error.getCode().startsWith("QUALITY_")) {
                pendingQualityRequestId = 0L;
                quality.setEnabled(true);
                showConfirmedQuality();
            }
            Toast.makeText(this, error.getMessage(), Toast.LENGTH_LONG).show();
            if (error.getFatal()) endSession(true);
        });
    }

    @Override public void onConnectionState(String state) {
        runOnUiThread(() -> {
            connectionState.setText(state);
            connectionState.setVisibility("Connected".equals(state) && hasRenderedFrame
                    ? View.GONE : View.VISIBLE);
        });
    }

    @Override public void onFirstFrame() {
        runOnUiThread(() -> {
            hasRenderedFrame = true;
            connectionState.setVisibility(View.GONE);
        });
    }

    @Override public void onStats(ReceiverStatsSnapshot stats) {
        runOnUiThread(() -> {
            latestStats = stats;
            updateMetrics();
        });
    }

    @Override public void onStreamError(Throwable error) {
        runOnUiThread(() -> {
            if (error instanceof DirectStreamSession.TerminalSocketException) {
                int code = ((DirectStreamSession.TerminalSocketException) error).statusCode;
                if (code == 404 || code == 4404) recoverSession();
                else endSession(true);
                return;
            }
            connectionState.setText(error.getMessage());
        });
    }

    private void showConfirmedQuality() {
        if (quality == null) return;
        int position = QualityPreference.spinnerPosition(confirmedQualityMode);
        if (quality.getSelectedItemPosition() != position) quality.setSelection(position, false);
    }

    private LinearLayout.LayoutParams weighted() {
        return new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1);
    }

    private LinearLayout.LayoutParams matchWrap(int height) {
        return new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, height);
    }

    private GradientDrawable panelBackground(int color, int radiusDp) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(color);
        drawable.setCornerRadius(dp(radiusDp));
        return drawable;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
