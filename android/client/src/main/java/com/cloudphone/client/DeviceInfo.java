package com.cloudphone.client;

import org.json.JSONObject;

/** A server-authorized Android device. The ADB serial is deliberately never exposed here. */
public final class DeviceInfo {
    public final String id;
    public final String name;
    public final String model;
    public final String androidVersion;
    public final String state;
    public final boolean authorized;
    public final boolean assistantReady;

    public DeviceInfo(String id, String name, String model, String androidVersion, String state,
                      boolean authorized, boolean assistantReady) {
        this.id = id;
        this.name = name;
        this.model = model;
        this.androidVersion = androidVersion;
        this.state = state;
        this.authorized = authorized;
        this.assistantReady = assistantReady;
    }

    public static DeviceInfo fromJson(JSONObject object) {
        return new DeviceInfo(
                object.optString("id"),
                object.optString("name", object.optString("model", "Android")),
                object.optString("model", "Android"),
                object.optString("androidVersion", ""),
                object.optString("state", "offline"),
                object.optBoolean("authorized"),
                object.optBoolean("assistantReady"));
    }

    public boolean isConnectable() {
        return authorized && ("online".equalsIgnoreCase(state) || "device".equalsIgnoreCase(state));
    }
}
