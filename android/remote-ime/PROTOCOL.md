# Remote IME local socket protocol

`RemoteInputMethodService` owns an Android abstract Unix-domain server socket. The Python server
configures it before switching the device default IME, then forwards it locally through ADB:

```powershell
$socket = "cloudphone_ime_<random-24-hex-char-suffix>"
$token = "<base64url-without-padding-of-32-random-bytes>"
adb -s <serial> shell am broadcast --include-stopped-packages --user 0 `
  -n com.cloudphone.ime/.SocketConfigReceiver `
  -a com.cloudphone.ime.CONFIGURE `
  --es socket $socket --es token $token
adb -s <serial> forward tcp:0 localabstract:$socket
```

The receiver requires `android.permission.DUMP`, which ADB shell has. The socket also accepts only
peer UID `root` or `shell` and requires the random token before any input command. Clear it at session
end with action `com.cloudphone.ime.CLEAR`, then restore the previously selected IME.

Every message is `u32be payloadLength | UTF-8 JSON`. A host frame is bounded at 128 KiB, and decoded
text is separately bounded at 16 KiB of UTF-8. The service never logs a text payload.

The first host message must be:

```json
{"type":"auth","token":"<base64url token>"}
```

The IME responds with `{"type":"auth_result","version":1,"accepted":true}`. A failed authentication
closes the connection. Once authenticated, the IME reports focus changes as:

```json
{"type":"focus","active":true,"inputType":1,"imeOptions":6,"password":false,"restarting":false}
```

On `onFinishInput`, it sends the same shape with `active:false`. The host submits local keyboard text
with `{"type":"commit","text":"...","editorAction":6}`. Text is committed through
`InputConnection.commitText()`; a nonzero editor action is then passed to `performEditorAction()`.
Each accepted operation produces a small `command_result` event, which the host may ignore.

The service sends an active focus snapshot immediately after successful authentication when a field
is already active. Self-drawn game input controls which bypass Android's input method system cannot
be detected by this helper.
