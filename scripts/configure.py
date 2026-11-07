from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import ipaddress
import secrets
import sys
import uuid
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "server"))

from cloudphone.passwords import hash_password
from cloudphone.config import _read_env_file


def quote_env(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def property_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:").replace("=", "\\=")


def build_certificate(public_ip: ipaddress.IPv4Address | ipaddress.IPv6Address):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, f"CloudPhone {public_ip}")]
    )
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=1825))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(public_ip)]), False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False
        )
        .sign(key, hashes.SHA256())
    )
    return key, cert


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a server-bound client configuration.")
    parser.add_argument("--public-ip", required=True)
    parser.add_argument("--public-port", type=int, default=8443)
    parser.add_argument("--admin-password")
    parser.add_argument("--adb-path")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--preserve-identity",
        action="store_true",
        help="replace the public certificate/address while retaining client and admin identity",
    )
    args = parser.parse_args()

    if args.force and args.preserve_identity:
        parser.error("--force and --preserve-identity are mutually exclusive")
    if args.admin_password and args.preserve_identity:
        parser.error("--admin-password cannot be used with --preserve-identity")

    public_ip = ipaddress.ip_address(args.public_ip)
    if not 1 <= args.public_port <= 65535:
        parser.error("public port must be between 1 and 65535")

    data_dir = REPO_ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    env_path = data_dir / "server.env"
    android_path = REPO_ROOT / "android" / "client-secrets.properties"
    existing = _read_env_file(env_path) if args.preserve_identity else {}
    if args.preserve_identity and not (env_path.is_file() and android_path.is_file()):
        parser.error("--preserve-identity requires an existing complete configuration")
    if not args.force and not args.preserve_identity and (env_path.exists() or android_path.exists()):
        parser.error("configuration already exists; pass --force to replace it")

    key, cert = build_certificate(public_ip)
    key_path = data_dir / "server-key.pem"
    cert_path = data_dir / "server-cert.pem"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    spki = cert.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    certificate_pin = "sha256/" + base64.b64encode(hashlib.sha256(spki).digest()).decode("ascii")
    if args.preserve_identity:
        required = (
            "CLOUDPHONE_CLIENT_ID",
            "CLOUDPHONE_CLIENT_PSK_BASE64",
            "CLOUDPHONE_ADMIN_PASSWORD_HASH",
        )
        missing = [name for name in required if not existing.get(name)]
        if missing:
            parser.error("existing configuration is missing: " + ", ".join(missing))
        client_id = existing["CLOUDPHONE_CLIENT_ID"]
        client_psk = existing["CLOUDPHONE_CLIENT_PSK_BASE64"]
        admin_hash = existing["CLOUDPHONE_ADMIN_PASSWORD_HASH"]
        admin_password = None
    else:
        client_id = str(uuid.uuid4())
        client_psk = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
        admin_password = args.admin_password or secrets.token_urlsafe(18)
        admin_hash = hash_password(admin_password)

    adb_path = args.adb_path or existing.get("CLOUDPHONE_ADB_PATH") or str(
        Path.home() / "Documents" / "plan" / "android-sdk" / "platform-tools" / "adb.exe"
    )
    values = {
        "CLOUDPHONE_PUBLIC_IP": str(public_ip),
        "CLOUDPHONE_PUBLIC_PORT": str(args.public_port),
        "CLOUDPHONE_TLS_CERT": str(cert_path),
        "CLOUDPHONE_TLS_KEY": str(key_path),
        "CLOUDPHONE_CLIENT_ID": client_id,
        "CLOUDPHONE_CLIENT_PSK_BASE64": client_psk,
        "CLOUDPHONE_CERT_SPKI_SHA256": certificate_pin,
        "CLOUDPHONE_ADMIN_PASSWORD_HASH": admin_hash,
        "CLOUDPHONE_ADB_PATH": adb_path,
        "CLOUDPHONE_SCRCPY_SERVER_PATH": existing.get(
            "CLOUDPHONE_SCRCPY_SERVER_PATH",
            str(REPO_ROOT / "dist" / "scrcpy-server-direct-v4.1"),
        ),
        "CLOUDPHONE_IME_APK_PATH": existing.get(
            "CLOUDPHONE_IME_APK_PATH", str(REPO_ROOT / "dist" / "cloudphone-remote-ime.apk")
        ),
        "CLOUDPHONE_DATABASE_PATH": existing.get(
            "CLOUDPHONE_DATABASE_PATH", str(data_dir / "cloudphone.db")
        ),
    }
    env_path.write_text(
        "\n".join(f"{key}={quote_env(value)}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )

    host = f"[{public_ip}]" if public_ip.version == 6 else str(public_ip)
    android_path.parent.mkdir(parents=True, exist_ok=True)
    android_values = {
        "serverUrl": f"https://{host}:{args.public_port}",
        "clientId": client_id,
        "clientPskBase64": client_psk,
        "certificatePin": certificate_pin,
    }
    android_path.write_text(
        "\n".join(f"{key}={property_value(value)}" for key, value in android_values.items()) + "\n",
        encoding="utf-8",
    )

    print(f"Server configuration: {env_path}")
    print(f"Android build configuration: {android_path}")
    print(f"Certificate pin: {certificate_pin}")
    if admin_password is not None and args.admin_password is None:
        print(f"Generated local admin password: {admin_password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
