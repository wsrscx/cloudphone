package com.cloudphone.client;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;

import org.junit.Test;

public final class PinnedCertificateTrustManagerTest {
    private static final String CERTIFICATE = ""
            + "-----BEGIN CERTIFICATE-----\n"
            + "MIIBfTCCASKgAwIBAgIUQUwwUk8sZwxSsRzVWhiky/0jJnEwCgYIKoZIzj0EAwIw\n"
            + "IjEgMB4GA1UEAwwXQ2xvdWRQaG9uZSAxOTIuMTY4LjEuMjMwHhcNMjYwNzI2MDU1\n"
            + "MzU2WhcNMzEwNzI1MDU1ODU2WjAiMSAwHgYDVQQDDBdDbG91ZFBob25lIDE5Mi4x\n"
            + "NjguMS4yMzBZMBMGByqGSM49AgEGCCqGSM49AwEHA0IABJDGmQG+b0zlkIq0MZlo\n"
            + "UvZsVNLfphq+9Tylp9G3rU1kWf27pyBz1zWB/Wap46rndwh2JKuPrp+NNAUt4o3o\n"
            + "fkajNjA0MA8GA1UdEQQIMAaHBMCoARcwDAYDVR0TAQH/BAIwADATBgNVHSUEDDAK\n"
            + "BggrBgEFBQcDATAKBggqhkjOPQQDAgNJADBGAiEAwo/cleoS67CViLB7DySDAC2C\n"
            + "O6LDa4KqkjDJjk4knUMCIQDRj2lml9k4MhG23V3/7e5Whdc6a7KInhyeF+zzfFTA\n"
            + "6g==\n"
            + "-----END CERTIFICATE-----\n";

    @Test public void matchesConfiguredServerSpki() throws Exception {
        X509Certificate certificate = parseCertificate();
        assertTrue(PinnedCertificateTrustManager.matchesCertificatePin(
                certificate, "sha256/Ui6dYWECd2CObW6U5qIis/+rQ+QbuvyTTcdTVffL6yw="));
    }

    @Test public void rejectsAnotherSpki() throws Exception {
        X509Certificate certificate = parseCertificate();
        assertFalse(PinnedCertificateTrustManager.matchesCertificatePin(
                certificate, "sha256/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="));
    }

    @Test(expected = IllegalArgumentException.class)
    public void rejectsMalformedPin() {
        new PinnedCertificateTrustManager("sha256/not-a-256-bit-hash");
    }

    private static X509Certificate parseCertificate() throws Exception {
        CertificateFactory factory = CertificateFactory.getInstance("X.509");
        return (X509Certificate) factory.generateCertificate(new ByteArrayInputStream(
                CERTIFICATE.getBytes(StandardCharsets.US_ASCII)));
    }
}
