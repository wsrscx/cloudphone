package com.cloudphone.remoteime;

import org.junit.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Base64;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

public class ImeWireProtocolTest {
    @Test
    public void acceptsOnlyMatchingToken() {
        byte[] token = new byte[32];
        for (int index = 0; index < token.length; index++) {
            token[index] = (byte) index;
        }
        String encoded = Base64.getUrlEncoder().withoutPadding().encodeToString(token);
        byte[] valid = ("{\"type\":\"auth\",\"token\":\"" + encoded + "\"}").getBytes(StandardCharsets.UTF_8);

        assertTrue(ImeWireProtocol.isValidAuth(valid, token));
        assertFalse(ImeWireProtocol.isValidAuth(
                "{\"type\":\"auth\",\"token\":\"AAAA\"}".getBytes(StandardCharsets.UTF_8), token));
    }

    @Test
    public void decodesUnicodeCommitTextAndEditorAction() throws Exception {
        byte[] frame = "{\"type\":\"commit\",\"text\":\"\\u4f60\\u597d e\\u0301 \\ud83d\\ude80\",\"editorAction\":6}"
                .getBytes(StandardCharsets.UTF_8);

        ImeWireProtocol.Command command = ImeWireProtocol.decodeCommand(frame);

        assertEquals(ImeWireProtocol.TYPE_COMMIT_TEXT, command.type);
        assertEquals("\u4f60\u597d e\u0301 \ud83d\ude80", command.text);
        assertEquals(6, command.editorAction);
    }

    @Test
    public void rejectsMalformedAndOversizedText() throws Exception {
        expectIo(new byte[]{'{', '"', (byte) 0xc3, (byte) 0x28});

        byte[] text = new byte[ImeWireProtocol.MAX_TEXT_BYTES + 1];
        Arrays.fill(text, (byte) 'a');
        byte[] prefix = "{\"type\":\"commit\",\"text\":\"".getBytes(StandardCharsets.UTF_8);
        byte[] suffix = "\"}".getBytes(StandardCharsets.UTF_8);
        byte[] frame = new byte[prefix.length + text.length + suffix.length];
        System.arraycopy(prefix, 0, frame, 0, prefix.length);
        System.arraycopy(text, 0, frame, prefix.length, text.length);
        System.arraycopy(suffix, 0, frame, prefix.length + text.length, suffix.length);
        expectIo(frame);
    }

    private static void expectIo(byte[] frame) throws Exception {
        try {
            ImeWireProtocol.decodeCommand(frame);
            fail("Expected IOException");
        } catch (IOException expected) {
            // Expected.
        }
    }
}
