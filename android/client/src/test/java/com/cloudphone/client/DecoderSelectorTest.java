package com.cloudphone.client;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.Arrays;
import java.util.List;

public final class DecoderSelectorTest {
    @Test public void android15AndMumuPreferSoftwareDecoding() {
        assertTrue(DecoderSelector.shouldPreferSoftware(35, "generic", "android", "phone", "x"));
        assertTrue(DecoderSelector.shouldPreferSoftware(34, "NetEase", "MuMu", "MuMu 12", "x"));
        assertFalse(DecoderSelector.shouldPreferSoftware(34, "Google", "google", "Pixel 8", "x"));
    }

    @Test public void softwarePreferenceKeepsHardwareAsFallback() {
        List<DecoderSelector.Candidate> ordered = DecoderSelector.ordered(Arrays.asList(
                new DecoderSelector.Candidate("c2.vendor.avc.decoder", false, true),
                new DecoderSelector.Candidate("OMX.google.h264.decoder", true, false),
                new DecoderSelector.Candidate("c2.android.avc.decoder", true, false)), true);

        assertEquals("c2.android.avc.decoder", ordered.get(0).name);
        assertEquals("OMX.google.h264.decoder", ordered.get(1).name);
        assertEquals("c2.vendor.avc.decoder", ordered.get(2).name);
    }

    @Test public void physicalDevicesPreferHardwareByDefault() {
        List<DecoderSelector.Candidate> ordered = DecoderSelector.ordered(Arrays.asList(
                new DecoderSelector.Candidate("c2.android.avc.decoder", true, false),
                new DecoderSelector.Candidate("c2.vendor.avc.decoder", false, true)), false);
        assertEquals("c2.vendor.avc.decoder", ordered.get(0).name);
    }
}
