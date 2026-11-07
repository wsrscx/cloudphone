package com.cloudphone.client;

import static org.junit.Assert.assertArrayEquals;

import org.junit.Test;

public final class AnnexBParameterSetsTest {
    @Test public void extractsThreeAndFourBytePrefixedParameterSets() {
        AnnexBParameterSets sets = AnnexBParameterSets.extract(new byte[]{
                0, 0, 1, 0x67, 1, 2,
                0, 0, 0, 1, 0x68, 3,
                0, 0, 1, 0x65, 4});

        assertArrayEquals(new byte[]{0, 0, 0, 1, 0x67, 1, 2}, sets.sps);
        assertArrayEquals(new byte[]{0, 0, 0, 1, 0x68, 3}, sets.pps);
    }
}
