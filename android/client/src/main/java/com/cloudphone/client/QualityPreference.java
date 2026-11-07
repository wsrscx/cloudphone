package com.cloudphone.client;

import android.content.Context;
import android.content.SharedPreferences;

import com.cloudphone.protocol.CloudPhoneProtocol.QualityMode;

/** Persists only the user's preferred video profile, never session credentials. */
public final class QualityPreference {
    private static final String PREFERENCES = "cloudphone_display";
    private static final String KEY_MODE = "quality_mode";

    private QualityPreference() {}

    public static QualityMode load(Context context) {
        int number = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
                .getInt(KEY_MODE, QualityMode.QUALITY_AUTO.getNumber());
        return validated(QualityMode.forNumber(number));
    }

    public static void save(Context context, QualityMode mode) {
        SharedPreferences preferences = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE);
        preferences.edit().putInt(KEY_MODE, validated(mode).getNumber()).apply();
    }

    public static QualityMode fromSpinnerPosition(int position) {
        switch (position) {
            case 1: return QualityMode.QUALITY_ORIGINAL;
            case 2: return QualityMode.QUALITY_1080P;
            case 3: return QualityMode.QUALITY_720P;
            case 4: return QualityMode.QUALITY_SMOOTH;
            default: return QualityMode.QUALITY_AUTO;
        }
    }

    public static int spinnerPosition(QualityMode mode) {
        switch (validated(mode)) {
            case QUALITY_ORIGINAL: return 1;
            case QUALITY_1080P: return 2;
            case QUALITY_720P: return 3;
            case QUALITY_SMOOTH: return 4;
            default: return 0;
        }
    }

    private static QualityMode validated(QualityMode mode) {
        if (mode == null || mode == QualityMode.UNRECOGNIZED
                || mode == QualityMode.QUALITY_MODE_UNSPECIFIED) {
            return QualityMode.QUALITY_AUTO;
        }
        return mode;
    }
}
