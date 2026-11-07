package com.cloudphone.remoteime;

import android.inputmethodservice.InputMethodService;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputConnection;

import java.lang.ref.WeakReference;

public class RemoteInputMethodService extends InputMethodService implements ImeSocketBridge.Callback {
    private static final Object ACTIVE_SERVICE_LOCK = new Object();
    private static WeakReference<RemoteInputMethodService> activeService = new WeakReference<>(null);

    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private ImeSocketBridge bridge;
    private EditorSnapshot currentEditor;

    static void onExternalConfigurationChanged() {
        RemoteInputMethodService service;
        synchronized (ACTIVE_SERVICE_LOCK) {
            service = activeService.get();
        }
        if (service != null) {
            service.mainHandler.post(service::reloadSession);
        }
    }

    @Override
    public void onCreate() {
        super.onCreate();
        synchronized (ACTIVE_SERVICE_LOCK) {
            activeService = new WeakReference<>(this);
        }
        reloadSession();
    }

    private void reloadSession() {
        if (bridge != null) {
            bridge.close();
            bridge = null;
        }
        SessionConfig config = SessionConfig.load(this);
        if (config == null) {
            return;
        }
        ImeSocketBridge newBridge = new ImeSocketBridge(config, this);
        bridge = newBridge;
        newBridge.start();
        if (currentEditor != null) {
            newBridge.sendInputStarted(currentEditor);
        }
    }

    @Override
    public View onCreateInputView() {
        return null;
    }

    @Override
    public boolean onEvaluateInputViewShown() {
        return false;
    }

    @Override
    public boolean onEvaluateFullscreenMode() {
        return false;
    }

    @Override
    public boolean onShowInputRequested(int flags, boolean configChange) {
        return false;
    }

    @Override
    public void onStartInput(EditorInfo attribute, boolean restarting) {
        super.onStartInput(attribute, restarting);
        EditorSnapshot snapshot = EditorSnapshot.from(attribute, restarting);
        currentEditor = snapshot;
        if (bridge != null) {
            bridge.sendInputStarted(snapshot);
        }
    }

    @Override
    public void onFinishInput() {
        EditorSnapshot snapshot = currentEditor;
        currentEditor = null;
        if (bridge != null && snapshot != null) {
            bridge.sendInputFinished(snapshot);
        }
        super.onFinishInput();
    }

    @Override
    public void commitText(String text, ImeSocketBridge.ResultCallback result) {
        mainHandler.post(() -> {
            InputConnection connection = getCurrentInputConnection();
            boolean success = false;
            if (connection != null && currentEditor != null) {
                try {
                    success = connection.commitText(text, 1);
                } catch (RuntimeException ignored) {
                    success = false;
                }
            }
            result.complete(success);
        });
    }

    @Override
    public void performEditorAction(int action, ImeSocketBridge.ResultCallback result) {
        mainHandler.post(() -> {
            InputConnection connection = getCurrentInputConnection();
            boolean success = false;
            if (connection != null && currentEditor != null) {
                try {
                    success = connection.performEditorAction(action);
                } catch (RuntimeException ignored) {
                    success = false;
                }
            }
            result.complete(success);
        });
    }

    @Override
    public void onDestroy() {
        if (bridge != null) {
            bridge.close();
            bridge = null;
        }
        synchronized (ACTIVE_SERVICE_LOCK) {
            if (activeService.get() == this) {
                activeService.clear();
            }
        }
        super.onDestroy();
    }
}
