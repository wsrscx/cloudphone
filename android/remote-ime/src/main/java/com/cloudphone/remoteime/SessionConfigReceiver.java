package com.cloudphone.remoteime;

import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public class SessionConfigReceiver extends BroadcastReceiver {
    public static final String ACTION_CONFIGURE = "com.cloudphone.ime.CONFIGURE";
    public static final String ACTION_CLEAR = "com.cloudphone.ime.CLEAR";
    public static final String EXTRA_SOCKET_NAME = "socket";
    public static final String EXTRA_SESSION_TOKEN = "token";

    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent.getAction();
        if (ACTION_CLEAR.equals(action)) {
            SessionConfig.clear(context);
            RemoteInputMethodService.onExternalConfigurationChanged();
            setResultCode(Activity.RESULT_OK);
            setResultData("cleared");
            return;
        }

        if (!ACTION_CONFIGURE.equals(action)) {
            setResultCode(Activity.RESULT_CANCELED);
            setResultData("unsupported action");
            return;
        }

        try {
            SessionConfig config = SessionConfig.parse(
                    intent.getStringExtra(EXTRA_SOCKET_NAME),
                    intent.getStringExtra(EXTRA_SESSION_TOKEN));
            SessionConfig.save(context, config);
            RemoteInputMethodService.onExternalConfigurationChanged();
            setResultCode(Activity.RESULT_OK);
            setResultData("configured");
        } catch (IllegalArgumentException exception) {
            setResultCode(Activity.RESULT_CANCELED);
            setResultData("invalid configuration");
        }
    }
}
