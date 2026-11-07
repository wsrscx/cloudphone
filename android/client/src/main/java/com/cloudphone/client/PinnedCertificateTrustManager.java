package com.cloudphone.client;

import java.security.cert.CertificateException;
import java.security.cert.X509Certificate;
import java.util.Base64;

import javax.net.ssl.X509TrustManager;

import okhttp3.CertificatePinner;

/** Trusts only the configured server public key; OkHttp still performs hostname verification. */
final class PinnedCertificateTrustManager implements X509TrustManager {
    private final String certificatePin;

    PinnedCertificateTrustManager(String certificatePin) {
        if (certificatePin == null || !certificatePin.startsWith("sha256/")
                || !isSha256Pin(certificatePin)) {
            throw new IllegalArgumentException("certificate pin must use sha256");
        }
        this.certificatePin = certificatePin;
    }

    @Override public void checkClientTrusted(X509Certificate[] chain, String authType)
            throws CertificateException {
        throw new CertificateException("client certificates are not supported");
    }

    @Override public void checkServerTrusted(X509Certificate[] chain, String authType)
            throws CertificateException {
        if (chain == null || chain.length == 0) {
            throw new CertificateException("server certificate chain is empty");
        }
        X509Certificate leaf = chain[0];
        leaf.checkValidity();
        if (!matchesCertificatePin(leaf, certificatePin)) {
            throw new CertificateException("server certificate public key does not match the configured pin");
        }
    }

    @Override public X509Certificate[] getAcceptedIssuers() {
        return new X509Certificate[0];
    }

    static boolean matchesCertificatePin(X509Certificate certificate, String expectedPin) {
        return expectedPin.equals(CertificatePinner.pin(certificate));
    }

    private static boolean isSha256Pin(String pin) {
        try {
            return Base64.getDecoder().decode(pin.substring("sha256/".length())).length == 32;
        } catch (IllegalArgumentException error) {
            return false;
        }
    }
}
