package com.cloudphone.client;

/** Minimal binary transport boundary used by the protobuf control protocol. */
public interface BinarySender {
    boolean isOpen();

    long queuedBytes();

    boolean send(byte[] payload);
}
