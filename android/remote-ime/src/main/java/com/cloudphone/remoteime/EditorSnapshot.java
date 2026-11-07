package com.cloudphone.remoteime;

import android.text.InputType;
import android.view.inputmethod.EditorInfo;

final class EditorSnapshot {
    final int inputType;
    final int imeOptions;
    final boolean password;
    final boolean restarting;

    EditorSnapshot(int inputType, int imeOptions, boolean password, boolean restarting) {
        this.inputType = inputType;
        this.imeOptions = imeOptions;
        this.password = password;
        this.restarting = restarting;
    }

    static EditorSnapshot from(EditorInfo editorInfo, boolean restarting) {
        int inputType = editorInfo == null ? InputType.TYPE_NULL : editorInfo.inputType;
        int imeOptions = editorInfo == null ? EditorInfo.IME_NULL : editorInfo.imeOptions;
        return new EditorSnapshot(inputType, imeOptions, isPasswordInputType(inputType), restarting);
    }

    static boolean isPasswordInputType(int inputType) {
        int inputClass = inputType & InputType.TYPE_MASK_CLASS;
        int variation = inputType & InputType.TYPE_MASK_VARIATION;
        if (inputClass == InputType.TYPE_CLASS_TEXT) {
            return variation == InputType.TYPE_TEXT_VARIATION_PASSWORD
                    || variation == InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
                    || variation == InputType.TYPE_TEXT_VARIATION_WEB_PASSWORD;
        }
        return inputClass == InputType.TYPE_CLASS_NUMBER
                && variation == InputType.TYPE_NUMBER_VARIATION_PASSWORD;
    }
}
