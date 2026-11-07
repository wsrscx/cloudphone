package com.cloudphone.remoteime;

import android.net.Credentials;
import android.net.LocalServerSocket;
import android.net.LocalSocket;

import java.io.IOException;
import java.io.OutputStream;
import java.util.Arrays;

final class ImeSocketBridge implements AutoCloseable {
    private static final int AUTH_TIMEOUT_MILLIS = 5_000;
    private static final int ROOT_UID = 0;
    private static final int SHELL_UID = 2_000;

    interface ResultCallback {
        void complete(boolean success);
    }

    interface Callback {
        void commitText(String text, ResultCallback result);

        void performEditorAction(int action, ResultCallback result);
    }

    private final SessionConfig config;
    private final Callback callback;
    private final Object connectionLock = new Object();

    private volatile boolean closed;
    private volatile EditorSnapshot activeEditor;
    private LocalServerSocket serverSocket;
    private LocalSocket clientSocket;
    private OutputStream authenticatedOutput;
    private Thread thread;

    ImeSocketBridge(SessionConfig config, Callback callback) {
        this.config = config;
        this.callback = callback;
    }

    void start() {
        thread = new Thread(this::runServer, "remote-ime-local-socket");
        thread.start();
    }

    void sendInputStarted(EditorSnapshot snapshot) {
        activeEditor = snapshot;
        sendAuthenticated(ImeWireProtocol.inputStarted(snapshot));
    }

    void sendInputFinished(EditorSnapshot snapshot) {
        activeEditor = null;
        sendAuthenticated(ImeWireProtocol.inputFinished(snapshot));
    }

    private void runServer() {
        try {
            LocalServerSocket listeningSocket = new LocalServerSocket(config.socketName());
            synchronized (connectionLock) {
                if (closed) {
                    listeningSocket.close();
                    return;
                }
                serverSocket = listeningSocket;
            }
            while (!closed) {
                LocalSocket accepted = listeningSocket.accept();
                if (!isAdbPeer(accepted)) {
                    closeQuietly(accepted);
                    continue;
                }
                handleClient(accepted);
            }
        } catch (IOException ignored) {
            // Closing either socket is the normal way to stop a blocking accept/read.
        } finally {
            synchronized (connectionLock) {
                serverSocket = null;
            }
        }
    }

    private boolean isAdbPeer(LocalSocket socket) {
        try {
            Credentials credentials = socket.getPeerCredentials();
            int uid = credentials.getUid();
            return uid == ROOT_UID || uid == SHELL_UID;
        } catch (IOException exception) {
            return false;
        }
    }

    private void handleClient(LocalSocket socket) {
        byte[] token = config.token();
        try {
            synchronized (connectionLock) {
                if (closed) {
                    return;
                }
                clientSocket = socket;
            }
            socket.setSoTimeout(AUTH_TIMEOUT_MILLIS);
            byte[] authFrame = FrameCodec.readFrame(socket.getInputStream());
            boolean authenticated = ImeWireProtocol.isValidAuth(authFrame, token);
            if (authFrame != null) {
                Arrays.fill(authFrame, (byte) 0);
            }
            FrameCodec.writeFrame(socket.getOutputStream(), ImeWireProtocol.authResult(authenticated));
            if (!authenticated) {
                return;
            }

            socket.setSoTimeout(0);
            synchronized (connectionLock) {
                if (closed) {
                    return;
                }
                authenticatedOutput = socket.getOutputStream();
                EditorSnapshot snapshot = activeEditor;
                if (snapshot != null) {
                    FrameCodec.writeFrame(authenticatedOutput, ImeWireProtocol.inputStarted(snapshot));
                }
            }

            while (!closed) {
                byte[] frame = FrameCodec.readFrame(socket.getInputStream());
                if (frame == null) {
                    return;
                }
                try {
                    dispatch(ImeWireProtocol.decodeCommand(frame));
                } finally {
                    Arrays.fill(frame, (byte) 0);
                }
            }
        } catch (IOException ignored) {
            // A malformed frame or disconnect only ends this authenticated client.
        } finally {
            Arrays.fill(token, (byte) 0);
            synchronized (connectionLock) {
                if (clientSocket == socket) {
                    authenticatedOutput = null;
                    clientSocket = null;
                }
            }
            closeQuietly(socket);
        }
    }

    private void dispatch(ImeWireProtocol.Command command) {
        switch (command.type) {
            case ImeWireProtocol.TYPE_COMMIT_TEXT:
                callback.commitText(command.text, this::sendCommandResult);
                if (command.editorAction != 0) {
                    callback.performEditorAction(command.editorAction, this::sendCommandResult);
                }
                break;
            case ImeWireProtocol.TYPE_PING:
                sendAuthenticated(ImeWireProtocol.pong());
                break;
            default:
                throw new AssertionError("Decoded unsupported command");
        }
    }

    private void sendCommandResult(boolean success) {
        sendAuthenticated(ImeWireProtocol.commandResult(success));
    }

    private void sendAuthenticated(byte[] frame) {
        synchronized (connectionLock) {
            if (authenticatedOutput == null) {
                return;
            }
            try {
                FrameCodec.writeFrame(authenticatedOutput, frame);
            } catch (IOException exception) {
                closeQuietly(clientSocket);
            }
        }
    }

    @Override
    public void close() {
        closed = true;
        synchronized (connectionLock) {
            authenticatedOutput = null;
            closeQuietly(clientSocket);
            clientSocket = null;
            closeQuietly(serverSocket);
            serverSocket = null;
        }
        if (thread != null) {
            thread.interrupt();
        }
    }

    private static void closeQuietly(LocalSocket socket) {
        if (socket == null) {
            return;
        }
        try {
            socket.close();
        } catch (IOException ignored) {
            // Best effort during disconnect.
        }
    }

    private static void closeQuietly(LocalServerSocket socket) {
        if (socket == null) {
            return;
        }
        try {
            socket.close();
        } catch (IOException ignored) {
            // Best effort during shutdown.
        }
    }
}
