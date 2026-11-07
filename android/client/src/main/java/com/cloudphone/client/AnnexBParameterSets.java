package com.cloudphone.client;

import java.util.Arrays;

/** Extracts SPS/PPS NAL units for MediaCodec configuration without parsing slice payloads. */
final class AnnexBParameterSets {
    final byte[] sps;
    final byte[] pps;

    private AnnexBParameterSets(byte[] sps, byte[] pps) {
        this.sps = sps;
        this.pps = pps;
    }

    static AnnexBParameterSets extract(byte[] accessUnit) {
        byte[] sps = null;
        byte[] pps = null;
        int start = findStartCode(accessUnit, 0);
        while (start >= 0) {
            int prefix = accessUnit[start + 2] == 1 ? 3 : 4;
            int nalStart = start + prefix;
            int next = findStartCode(accessUnit, nalStart);
            int end = next >= 0 ? next : accessUnit.length;
            if (nalStart < end) {
                int type = accessUnit[nalStart] & 0x1f;
                byte[] nal = withFourBytePrefix(accessUnit, nalStart, end);
                if (type == 7 && sps == null) sps = nal;
                else if (type == 8 && pps == null) pps = nal;
            }
            if (sps != null && pps != null) break;
            start = next;
        }
        return new AnnexBParameterSets(sps, pps);
    }

    private static byte[] withFourBytePrefix(byte[] source, int start, int end) {
        byte[] result = new byte[end - start + 4];
        result[3] = 1;
        System.arraycopy(source, start, result, 4, end - start);
        return result;
    }

    private static int findStartCode(byte[] value, int from) {
        for (int index = Math.max(0, from); index + 3 <= value.length; index++) {
            if (value[index] != 0 || value[index + 1] != 0) continue;
            if (value[index + 2] == 1) return index;
            if (index + 3 < value.length && value[index + 2] == 0 && value[index + 3] == 1) {
                return index;
            }
        }
        return -1;
    }
}
