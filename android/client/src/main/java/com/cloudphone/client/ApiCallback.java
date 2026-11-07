package com.cloudphone.client;

public interface ApiCallback<T> {
    void onSuccess(T value);

    void onFailure(Throwable error);
}

