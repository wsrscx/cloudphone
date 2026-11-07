from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from cloudphone.config import AppConfig, _read_env_file


class AppConfigTests(unittest.TestCase):
    def test_quoted_windows_paths_are_unescaped_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / "server.env"
            env_file.write_text(
                'CLOUDPHONE_ADB_PATH="C:\\\\Tools\\\\adb.exe"\n', encoding="utf-8"
            )

            values = _read_env_file(env_file)

        self.assertEqual(values["CLOUDPHONE_ADB_PATH"], r"C:\Tools\adb.exe")

    def test_direct_scrcpy_is_default_and_legacy_media_settings_are_ignored(self) -> None:
        secret = base64.b64encode(bytes(range(32))).decode("ascii")
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / "server.env"
            env_file.write_text(
                "\n".join(
                    (
                        f"CLOUDPHONE_CLIENT_PSK_BASE64={secret}",
                        f"CLOUDPHONE_SERVER_SECRET_BASE64={secret}",
                        "CLOUDPHONE_DEVICE_AGENT_ENABLED=true",
                        "CLOUDPHONE_DEVICE_AGENT_KEY_PATH=missing.key",
                        "CLOUDPHONE_DEVICE_AGENT_SERIALS=emulator-5562",
                        "CLOUDPHONE_WEBRTC_PORT=0",
                    )
                ),
                encoding="utf-8",
            )

            config = AppConfig.load(env_file)

        self.assertEqual(config.scrcpy_server_path.name, "scrcpy-server-direct-v4.1")
        self.assertFalse(hasattr(config, "webrtc_port"))
        self.assertFalse(hasattr(config, "device_agent_key_path"))
