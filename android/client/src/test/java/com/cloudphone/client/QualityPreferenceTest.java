package com.cloudphone.client;

import static org.junit.Assert.assertEquals;

import com.cloudphone.protocol.CloudPhoneProtocol.QualityMode;

import org.junit.Test;

public final class QualityPreferenceTest {
    @Test public void spinnerMappingsRoundTripEverySupportedMode() {
        for (int position = 0; position <= 4; position++) {
            QualityMode mode = QualityPreference.fromSpinnerPosition(position);
            assertEquals(position, QualityPreference.spinnerPosition(mode));
        }
    }

    @Test public void invalidSpinnerPositionFallsBackToAuto() {
        assertEquals(QualityMode.QUALITY_AUTO, QualityPreference.fromSpinnerPosition(99));
    }
}
