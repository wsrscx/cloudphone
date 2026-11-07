package com.cloudphone.client;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;

/** Pure decoder ordering policy; Android codec discovery is kept in the decoder adapter. */
final class DecoderSelector {
    static final class Candidate {
        final String name;
        final boolean software;
        final boolean hardware;

        Candidate(String name, boolean software, boolean hardware) {
            this.name = name;
            this.software = software;
            this.hardware = hardware;
        }
    }

    private DecoderSelector() {}

    static boolean shouldPreferSoftware(int sdkInt, String manufacturer, String brand,
                                        String model, String fingerprint) {
        if (sdkInt >= 35) return true;
        String identity = safe(manufacturer) + " " + safe(brand) + " " + safe(model)
                + " " + safe(fingerprint);
        identity = identity.toLowerCase(Locale.US);
        return identity.contains("mumu") || identity.contains("netease")
                || identity.contains("microvirt") || identity.contains("nemu");
    }

    static List<Candidate> ordered(List<Candidate> values, boolean preferSoftware) {
        List<Candidate> result = new ArrayList<>(values);
        result.sort(Comparator.comparingInt((Candidate value) -> score(value, preferSoftware))
                .thenComparing(value -> value.name));
        return result;
    }

    private static int score(Candidate value, boolean preferSoftware) {
        String name = value.name.toLowerCase(Locale.US);
        int implementation;
        if (name.startsWith("c2.android.")) implementation = 0;
        else if (name.startsWith("omx.google.")) implementation = 1;
        else if (value.software) implementation = 2;
        else if (value.hardware) implementation = 3;
        else implementation = 4;
        if (preferSoftware) return (value.software ? 0 : 100) + implementation;
        return (value.hardware ? 0 : value.software ? 100 : 50) + implementation;
    }

    private static String safe(String value) {
        return value == null ? "" : value;
    }
}
