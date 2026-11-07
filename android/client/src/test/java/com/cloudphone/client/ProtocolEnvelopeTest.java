package com.cloudphone.client;

import static org.junit.Assert.assertEquals;

import com.cloudphone.protocol.CloudPhoneProtocol.Envelope;
import com.cloudphone.protocol.CloudPhoneProtocol.TouchMoveBatch;
import com.cloudphone.protocol.CloudPhoneProtocol.TouchPoint;

import org.junit.Test;

public final class ProtocolEnvelopeTest {
    @Test public void preservesTenPointerMoveBatch() throws Exception {
        TouchMoveBatch.Builder moves = TouchMoveBatch.newBuilder();
        for (int pointer = 0; pointer < 10; pointer++) {
            moves.addPoints(TouchPoint.newBuilder()
                    .setPointerId(pointer)
                    .setNormalizedX(pointer * 1_000)
                    .setNormalizedY(65_535 - pointer * 1_000)
                    .setNormalizedPressure(32_768));
        }
        Envelope encoded = Envelope.newBuilder().setProtocolVersion(1).setSequence(42)
                .setVideoGeneration(3).setTouchMoves(moves).build();
        Envelope decoded = Envelope.parseFrom(encoded.toByteArray());
        assertEquals(10, decoded.getTouchMoves().getPointsCount());
        assertEquals(9, decoded.getTouchMoves().getPoints(9).getPointerId());
        assertEquals(3, decoded.getVideoGeneration());
    }
}

