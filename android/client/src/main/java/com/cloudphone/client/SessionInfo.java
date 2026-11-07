package com.cloudphone.client;

/** All values originate in a trusted, authenticated create-session response. */
public final class SessionInfo {
    public final String deviceId;
    public final String sessionId;
    public final String controlUrl;
    public final String videoUrl;

    public SessionInfo(String deviceId, String sessionId, String controlUrl, String videoUrl) {
        this.deviceId = deviceId;
        this.sessionId = sessionId;
        this.controlUrl = controlUrl;
        this.videoUrl = videoUrl;
    }
}
