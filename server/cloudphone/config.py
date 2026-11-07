from __future__ import annotations

import base64
import os
import secrets
from dataclasses import dataclass
from pathlib import Path


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            quote = value[0]
            value = value[1:-1]
            if quote == '"':
                output: list[str] = []
                index = 0
                while index < len(value):
                    if value[index] == "\\" and index + 1 < len(value) and value[index + 1] in {'"', "\\"}:
                        index += 1
                    output.append(value[index])
                    index += 1
                value = "".join(output)
        values[key.strip()] = value
    return values


def _decode_secret(value: str, name: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise ValueError(f"{name} must be valid Base64") from exc
    if len(decoded) != 32:
        raise ValueError(f"{name} must decode to exactly 32 bytes")
    return decoded


def _load_or_create(path: Path) -> bytes:
    if path.is_file():
        value = path.read_bytes()
        if len(value) != 32:
            raise ValueError(f"invalid 32-byte secret file: {path}")
        return value
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_bytes(32)
    path.write_bytes(value)
    return value


@dataclass(frozen=True, slots=True)
class AppConfig:
    root_dir: Path
    data_dir: Path
    database_path: Path
    adb_path: Path
    scrcpy_server_path: Path
    ime_apk_path: Path
    tls_cert: Path | None
    tls_key: Path | None
    public_ip: str
    public_port: int
    admin_host: str
    admin_port: int
    client_id: str
    client_psk: bytes
    server_secret: bytes
    cert_spki_sha256: str
    admin_password_hash: str
    token_ttl_seconds: int = 86_400
    challenge_ttl_seconds: int = 120

    @property
    def public_base_url(self) -> str:
        scheme = "https" if self.tls_cert and self.tls_key else "http"
        return f"{scheme}://{self.public_url_host}:{self.public_port}"

    @property
    def public_url_host(self) -> str:
        return f"[{self.public_ip}]" if ":" in self.public_ip and not self.public_ip.startswith("[") else self.public_ip

    @classmethod
    def load(cls, env_file: str | Path | None = None) -> "AppConfig":
        root = Path(__file__).resolve().parents[2]
        chosen_env = Path(env_file) if env_file else root / "data" / "server.env"
        file_values = _read_env_file(chosen_env)

        def setting(key: str, default: str = "") -> str:
            return os.environ.get(key, file_values.get(key, default)).strip()

        data_dir = root / "data"
        database_path = Path(setting("CLOUDPHONE_DATABASE_PATH", str(data_dir / "cloudphone.db")))
        psk_text = setting("CLOUDPHONE_CLIENT_PSK_BASE64")
        client_psk = (
            _decode_secret(psk_text, "CLOUDPHONE_CLIENT_PSK_BASE64")
            if psk_text
            else _load_or_create(data_dir / "client-psk.bin")
        )
        server_secret_text = setting("CLOUDPHONE_SERVER_SECRET_BASE64")
        server_secret = (
            _decode_secret(server_secret_text, "CLOUDPHONE_SERVER_SECRET_BASE64")
            if server_secret_text
            else _load_or_create(data_dir / "server-secret.bin")
        )
        cert_text = setting("CLOUDPHONE_TLS_CERT")
        key_text = setting("CLOUDPHONE_TLS_KEY")
        config = cls(
            root_dir=root,
            data_dir=data_dir,
            database_path=database_path,
            adb_path=Path(setting("CLOUDPHONE_ADB_PATH", "adb")),
            scrcpy_server_path=Path(
                setting("CLOUDPHONE_SCRCPY_SERVER_PATH", str(root / "dist" / "scrcpy-server-direct-v4.1"))
            ),
            ime_apk_path=Path(
                setting("CLOUDPHONE_IME_APK_PATH", str(root / "dist" / "cloudphone-remote-ime.apk"))
            ),
            tls_cert=Path(cert_text) if cert_text else None,
            tls_key=Path(key_text) if key_text else None,
            public_ip=setting("CLOUDPHONE_PUBLIC_IP", "127.0.0.1"),
            public_port=int(setting("CLOUDPHONE_PUBLIC_PORT", "8443")),
            admin_host="127.0.0.1",
            admin_port=int(setting("CLOUDPHONE_ADMIN_PORT", "8080")),
            client_id=setting("CLOUDPHONE_CLIENT_ID", "cloudphone-client"),
            client_psk=client_psk,
            server_secret=server_secret,
            cert_spki_sha256=setting("CLOUDPHONE_CERT_SPKI_SHA256"),
            admin_password_hash=setting("CLOUDPHONE_ADMIN_PASSWORD_HASH"),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not self.client_id or len(self.client_id) > 128:
            raise ValueError("CLOUDPHONE_CLIENT_ID must contain 1-128 characters")
        if not 1 <= self.public_port <= 65535:
            raise ValueError("CLOUDPHONE_PUBLIC_PORT must be in the range 1-65535")
        if self.admin_host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("the management application must only listen on loopback")
        if bool(self.tls_cert) != bool(self.tls_key):
            raise ValueError("TLS certificate and private key must be configured together")
