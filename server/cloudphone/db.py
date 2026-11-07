from __future__ import annotations

import contextlib
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True, slots=True)
class DeviceRecord:
    id: str
    serial: str
    alias: str
    model: str
    android_version: str
    state: str
    authorized: bool
    assistant_ready: bool
    last_seen_ms: int

    def public_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.alias or self.model or "Android device",
            "model": self.model,
            "androidVersion": self.android_version,
            "state": self.state,
            "authorized": self.authorized,
            "assistantReady": self.assistant_ready,
        }


@dataclass(frozen=True, slots=True)
class ChallengeRecord:
    id: str
    client_id: str
    nonce: str
    expires_at_ms: int


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._migrate()

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            yield connection
        finally:
            connection.close()

    def _migrate(self) -> None:
        with self._lock, self._connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY,
                    serial TEXT NOT NULL UNIQUE,
                    alias TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    android_version TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'offline',
                    authorized INTEGER NOT NULL DEFAULT 0 CHECK (authorized IN (0, 1)),
                    assistant_ready INTEGER NOT NULL DEFAULT 0 CHECK (assistant_ready IN (0, 1)),
                    last_seen_ms INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS client_binding (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    client_id TEXT NOT NULL,
                    public_key_der BLOB NOT NULL,
                    fingerprint TEXT NOT NULL,
                    bound_at_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_challenges (
                    id TEXT PRIMARY KEY,
                    client_id TEXT NOT NULL,
                    nonce TEXT NOT NULL,
                    expires_at_ms INTEGER NOT NULL,
                    consumed INTEGER NOT NULL DEFAULT 0 CHECK (consumed IN (0, 1))
                );
                CREATE TABLE IF NOT EXISTS ime_restore (
                    device_id TEXT PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
                    previous_ime TEXT NOT NULL,
                    socket_name TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                    updated_at_ms INTEGER NOT NULL
                );
                """
            )

    @staticmethod
    def _device(row: sqlite3.Row | None) -> DeviceRecord | None:
        if row is None:
            return None
        return DeviceRecord(
            id=row["id"],
            serial=row["serial"],
            alias=row["alias"],
            model=row["model"],
            android_version=row["android_version"],
            state=row["state"],
            authorized=bool(row["authorized"]),
            assistant_ready=bool(row["assistant_ready"]),
            last_seen_ms=row["last_seen_ms"],
        )

    def upsert_device(self, device_id: str, serial: str, state: str, model: str = "", android_version: str = "") -> DeviceRecord:
        now = int(time.time() * 1000)
        with self._lock, self._connect() as db:
            db.execute(
                """
                INSERT INTO devices(id, serial, model, android_version, state, last_seen_ms)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(serial) DO UPDATE SET
                    model=CASE WHEN excluded.model='' THEN devices.model ELSE excluded.model END,
                    android_version=CASE WHEN excluded.android_version='' THEN devices.android_version ELSE excluded.android_version END,
                    state=excluded.state,
                    last_seen_ms=excluded.last_seen_ms
                """,
                (device_id, serial, model, android_version, state, now),
            )
            return self._device(db.execute("SELECT * FROM devices WHERE serial=?", (serial,)).fetchone())  # type: ignore[return-value]

    def mark_missing_offline(self, present_serials: set[str]) -> None:
        with self._lock, self._connect() as db:
            if present_serials:
                placeholders = ",".join("?" for _ in present_serials)
                db.execute(f"UPDATE devices SET state='offline', assistant_ready=0 WHERE serial NOT IN ({placeholders})", tuple(present_serials))
            else:
                db.execute("UPDATE devices SET state='offline', assistant_ready=0")

    def list_devices(self, authorized_only: bool = False) -> list[DeviceRecord]:
        query = "SELECT * FROM devices"
        if authorized_only:
            query += " WHERE authorized=1"
        query += " ORDER BY CASE WHEN state='device' THEN 0 ELSE 1 END, COALESCE(NULLIF(alias,''), model), id"
        with self._lock, self._connect() as db:
            return [self._device(row) for row in db.execute(query).fetchall()]  # type: ignore[misc]

    def get_device(self, device_id: str) -> DeviceRecord | None:
        with self._lock, self._connect() as db:
            return self._device(db.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone())

    def update_device(self, device_id: str, *, alias: str | None = None, authorized: bool | None = None, assistant_ready: bool | None = None) -> DeviceRecord | None:
        values: list[object] = []
        assignments: list[str] = []
        if alias is not None:
            assignments.append("alias=?")
            values.append(alias.strip()[:80])
        if authorized is not None:
            assignments.append("authorized=?")
            values.append(int(authorized))
        if assistant_ready is not None:
            assignments.append("assistant_ready=?")
            values.append(int(assistant_ready))
        if not assignments:
            return self.get_device(device_id)
        values.append(device_id)
        with self._lock, self._connect() as db:
            db.execute(f"UPDATE devices SET {', '.join(assignments)} WHERE id=?", values)
            return self._device(db.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone())

    def create_challenge(self, challenge: ChallengeRecord) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM auth_challenges WHERE expires_at_ms < ? OR consumed=1", (int(time.time() * 1000),))
            db.execute(
                "INSERT INTO auth_challenges(id, client_id, nonce, expires_at_ms) VALUES (?, ?, ?, ?)",
                (challenge.id, challenge.client_id, challenge.nonce, challenge.expires_at_ms),
            )

    def consume_challenge(self, challenge_id: str, client_id: str, now_ms: int) -> ChallengeRecord | None:
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM auth_challenges WHERE id=? AND client_id=? AND consumed=0 AND expires_at_ms>=?",
                (challenge_id, client_id, now_ms),
            ).fetchone()
            if row is None:
                db.execute("ROLLBACK")
                return None
            db.execute("UPDATE auth_challenges SET consumed=1 WHERE id=?", (challenge_id,))
            db.execute("COMMIT")
            return ChallengeRecord(row["id"], row["client_id"], row["nonce"], row["expires_at_ms"])

    def bind_or_verify_client(self, client_id: str, public_key_der: bytes, fingerprint: str) -> bool:
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM client_binding WHERE singleton=1").fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO client_binding(singleton, client_id, public_key_der, fingerprint, bound_at_ms) VALUES (1, ?, ?, ?, ?)",
                    (client_id, public_key_der, fingerprint, int(time.time() * 1000)),
                )
                db.execute("COMMIT")
                return True
            matches = row["client_id"] == client_id and bytes(row["public_key_der"]) == public_key_der
            db.execute("COMMIT")
            return matches

    def get_client_binding(self) -> dict[str, object] | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM client_binding WHERE singleton=1").fetchone()
            return dict(row) if row else None

    def reset_client_binding(self) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM client_binding")
            db.execute("DELETE FROM auth_challenges")

    def save_ime_restore(self, device_id: str, previous_ime: str, socket_name: str) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                """
                INSERT INTO ime_restore(device_id, previous_ime, socket_name, active, updated_at_ms)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(device_id) DO UPDATE SET previous_ime=excluded.previous_ime,
                    socket_name=excluded.socket_name, active=1, updated_at_ms=excluded.updated_at_ms
                """,
                (device_id, previous_ime, socket_name, int(time.time() * 1000)),
            )

    def active_ime_restores(self) -> list[dict[str, object]]:
        with self._lock, self._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM ime_restore WHERE active=1").fetchall()]

    def finish_ime_restore(self, device_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE ime_restore SET active=0, updated_at_ms=? WHERE device_id=?", (int(time.time() * 1000), device_id))
