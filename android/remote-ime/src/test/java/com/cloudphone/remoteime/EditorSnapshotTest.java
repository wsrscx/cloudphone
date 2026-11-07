package com.cloudphone.remoteime;

import android.text.InputType;

import org.junit.Test;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class EditorSnapshotTest {
    @Test
    public void recognizesTextAndNumberPasswordEditors() {
        assertTrue(EditorSnapshot.isPasswordInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD));
        assertTrue(EditorSnapshot.isPasswordInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_WEB_PASSWORD));
        assertTrue(EditorSnapshot.isPasswordInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD));
        assertFalse(EditorSnapshot.isPasswordInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS));
    }
}
