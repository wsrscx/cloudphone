from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .auth import AuthService, AuthenticatedClient, AuthenticationError
from .config import AppConfig
from .db import Database
from .devices import AdbError, AdbRunner, DeviceTracker
from .direct_session import (
    ControlStreamClosed,
    DirectSession,
    SessionError,
    SessionManager,
)
from .direct_stream import DirectStreamClosed
from .ime import restore_abandoned_imes
from .passwords import verify_password
from .quality import QualityMode
from .scrcpy import ScrcpyProtocolError


logger = logging.getLogger(__name__)


def _json_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


async def _body(request: Request, maximum: int = 32 * 1024) -> dict[str, object]:
    raw = await request.body()
    if len(raw) > maximum:
        raise _json_error(413, "BODY_TOO_LARGE", "request body is too large")
    try:
        value = json.loads(raw or b"{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _json_error(400, "INVALID_JSON", "request body must be a JSON object") from exc
    if not isinstance(value, dict):
        raise _json_error(400, "INVALID_JSON", "request body must be a JSON object")
    return value


@dataclass(slots=True)
class ServiceContext:
    config: AppConfig
    db: Database
    auth: AuthService
    adb: AdbRunner
    tracker: DeviceTracker
    sessions: SessionManager
    _references: int = 0
    _lifecycle_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @classmethod
    def build(cls, config: AppConfig | None = None) -> "ServiceContext":
        config = config or AppConfig.load()
        db = Database(config.database_path)
        adb = AdbRunner(config.adb_path)
        auth = AuthService(
            db,
            expected_client_id=config.client_id,
            client_psk=config.client_psk,
            server_secret=config.server_secret,
            challenge_ttl_seconds=config.challenge_ttl_seconds,
            token_ttl_seconds=config.token_ttl_seconds,
        )
        tracker = DeviceTracker(db, adb, config.server_secret)
        sessions = SessionManager(db, adb, config.scrcpy_server_path, config.ime_apk_path)
        tracker.add_listener(
            lambda _devices: restore_abandoned_imes(
                db,
                adb,
                {sessions.current.device.id} if sessions.current else set(),
            )
        )
        tracker.add_listener(sessions.device_snapshot_changed)
        return cls(config, db, auth, adb, tracker, sessions)

    async def acquire(self) -> None:
        async with self._lifecycle_lock:
            self._references += 1
            if self._references != 1:
                return
            await restore_abandoned_imes(self.db, self.adb)
            await self.tracker.start()

    async def release(self) -> None:
        async with self._lifecycle_lock:
            self._references = max(0, self._references - 1)
            if self._references:
                return
            await self.sessions.shutdown()
            await self.tracker.stop()


class AdminSessionAuth:
    COOKIE = "cloudphone_admin"

    def __init__(self, password_hash: str, secret: bytes):
        self.password_hash = password_hash
        self.secret = secret

    def login(self, password: str) -> tuple[str, str]:
        if self.password_hash and not verify_password(self.password_hash, password):
            raise _json_error(401, "INVALID_ADMIN_PASSWORD", "管理密码错误")
        csrf = secrets.token_urlsafe(24)
        payload = json.dumps(
            {"v": 1, "exp": int(time.time()) + 3600, "csrf": csrf, "jti": secrets.token_urlsafe(12)},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
        signature = hmac.new(self.secret, b"admin-session\0" + encoded, hashlib.sha256).digest()
        token = encoded.decode("ascii") + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
        return token, csrf

    def authenticate(self, request: Request, require_csrf: bool) -> dict[str, object]:
        token = request.cookies.get(self.COOKIE, "")
        try:
            encoded_text, signature_text = token.split(".", 1)
            encoded = encoded_text.encode("ascii")
            signature = base64.urlsafe_b64decode(signature_text + "=" * (-len(signature_text) % 4))
            expected = hmac.new(self.secret, b"admin-session\0" + encoded, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            payload = json.loads(base64.urlsafe_b64decode(encoded_text + "=" * (-len(encoded_text) % 4)))
            if payload.get("v") != 1 or int(payload.get("exp", 0)) < int(time.time()):
                raise ValueError
            if require_csrf and not hmac.compare_digest(str(payload.get("csrf", "")), request.headers.get("X-CSRF-Token", "")):
                raise _json_error(403, "INVALID_CSRF", "CSRF token is missing or invalid")
            return payload
        except HTTPException:
            raise
        except (ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
            raise _json_error(401, "ADMIN_LOGIN_REQUIRED", "请先登录本地管理页") from exc


def _bearer(request: Request, auth: AuthService) -> AuthenticatedClient:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise _json_error(401, "AUTH_REQUIRED", "missing bearer token")
    try:
        return auth.authenticate_token(header[7:])
    except AuthenticationError as exc:
        raise _json_error(401, "INVALID_TOKEN", str(exc)) from exc


def _websocket_bearer(websocket: WebSocket) -> str:
    authorization = websocket.headers.get("Authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if separator and scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return websocket.query_params.get("token", "")


async def _run_control_socket(websocket: WebSocket, session: DirectSession) -> None:
    try:
        connection = await session.attach_control()
    except SessionError:
        await websocket.close(code=4404, reason="session not found")
        return

    async def send_output() -> str:
        try:
            while True:
                await websocket.send_bytes(await connection.outgoing.receive())
        except ControlStreamClosed:
            return "superseded"
        except (WebSocketDisconnect, OSError, RuntimeError):
            return "disconnected"

    async def receive_input() -> str:
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    return "disconnected"
                data = message.get("bytes")
                if not isinstance(data, bytes):
                    with suppress(Exception):
                        await websocket.close(code=4400, reason="binary control messages required")
                    return "closed"
                try:
                    await session.receive_control(data, connection.epoch)
                except ControlStreamClosed:
                    return "superseded"
        except (WebSocketDisconnect, OSError, RuntimeError):
            return "disconnected"

    tasks = {
        asyncio.create_task(send_output(), name=f"control-send-{session.id}"),
        asyncio.create_task(receive_input(), name=f"control-receive-{session.id}"),
    }
    outcome = "disconnected"
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        results = await asyncio.gather(*done, *pending, return_exceptions=True)
        if "superseded" in results:
            outcome = "superseded"
        error = next((result for result in results if isinstance(result, Exception)), None)
        if error is not None and not isinstance(error, asyncio.CancelledError):
            raise error
    except asyncio.CancelledError:
        raise
    except Exception:
        outcome = "failed"
        logger.exception("control WebSocket failed for session %s", session.id)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await session.detach(connection)
        code = 4409 if outcome == "superseded" else (1000 if outcome == "closed" else 1001)
        if outcome == "failed":
            code = 1011
        with suppress(Exception):
            await websocket.close(code=code, reason=f"control {outcome}")


async def _run_video_socket(websocket: WebSocket, session: DirectSession) -> None:
    try:
        connection = await session.attach_video()
    except SessionError:
        await websocket.close(code=4404, reason="session not found")
        return

    async def send_video() -> str:
        try:
            while True:
                packet = await connection.incoming.receive()
                encoded = packet.encode()
                await websocket.send_bytes(encoded)
                session.record_video_sent(packet.kind, len(encoded))
        except DirectStreamClosed:
            return "superseded"
        except (WebSocketDisconnect, OSError, RuntimeError):
            return "disconnected"

    async def receive_disconnect() -> str:
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    return "disconnected"
                with suppress(Exception):
                    await websocket.close(code=4400, reason="video socket is send-only")
                return "closed"
        except (WebSocketDisconnect, OSError, RuntimeError):
            return "disconnected"

    tasks = {
        asyncio.create_task(send_video(), name=f"video-send-{session.id}"),
        asyncio.create_task(receive_disconnect(), name=f"video-receive-{session.id}"),
    }
    outcome = "disconnected"
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        results = await asyncio.gather(*done, *pending, return_exceptions=True)
        if "superseded" in results:
            outcome = "superseded"
        error = next((result for result in results if isinstance(result, Exception)), None)
        if error is not None and not isinstance(error, asyncio.CancelledError):
            raise error
    except asyncio.CancelledError:
        raise
    except Exception:
        outcome = "failed"
        logger.exception("video WebSocket failed for session %s", session.id)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await session.detach(connection)
        code = 4409 if outcome == "superseded" else (1000 if outcome == "closed" else 1001)
        if outcome == "failed":
            code = 1011
        with suppress(Exception):
            await websocket.close(code=code, reason=f"video {outcome}")


def create_public_app(context: ServiceContext) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await context.acquire()
        try:
            yield
        finally:
            await context.release()

    app = FastAPI(title="CloudPhone Public API", version="1.0", docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def secure_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {"status": "ok", "activeSession": bool(context.sessions.current)}

    @app.post("/api/v1/auth/challenge")
    async def challenge(request: Request) -> dict[str, object]:
        body = await _body(request, 4096)
        try:
            return context.auth.issue_challenge(str(body.get("clientId", "")))
        except AuthenticationError as exc:
            raise _json_error(401, "AUTH_FAILED", str(exc)) from exc

    @app.post("/api/v1/auth/verify")
    async def verify(request: Request) -> dict[str, object]:
        body = await _body(request)
        try:
            return context.auth.verify(
                client_id=str(body.get("clientId", "")),
                challenge_id=str(body.get("challengeId", "")),
                timestamp_ms=int(body.get("timestampMs", 0)),
                hmac_b64=str(body.get("hmac", "")),
                public_key_b64=str(body.get("publicKey", "")),
                key_signature_b64=str(body.get("keySignature", "")),
            )
        except (AuthenticationError, TypeError, ValueError) as exc:
            raise _json_error(401, "AUTH_FAILED", str(exc)) from exc

    @app.get("/api/v1/devices")
    async def devices(request: Request) -> dict[str, object]:
        _bearer(request, context.auth)
        return {"devices": [device.public_dict() for device in context.db.list_devices(authorized_only=True)]}

    @app.post("/api/v1/sessions", status_code=201)
    async def create_session(request: Request) -> dict[str, object]:
        client = _bearer(request, context.auth)
        body = await _body(request, 4096)
        device_id = str(body.get("deviceId", ""))
        if not device_id.startswith("dev_") or len(device_id) > 64:
            raise _json_error(400, "INVALID_DEVICE", "invalid deviceId")
        raw_quality_mode = body.get("qualityMode", body.get("mode", 1))
        if isinstance(raw_quality_mode, bool):
            raise _json_error(400, "INVALID_QUALITY_MODE", "invalid quality mode")
        try:
            quality_mode = QualityMode(int(raw_quality_mode))
        except (TypeError, ValueError):
            raise _json_error(400, "INVALID_QUALITY_MODE", "invalid quality mode") from None
        try:
            session = await context.sessions.create(device_id, client.client_id, quality_mode)
        except SessionError as exc:
            raise _json_error(409, "SESSION_UNAVAILABLE", str(exc)) from exc
        except (FileNotFoundError, AdbError, ScrcpyProtocolError) as exc:
            raise _json_error(503, "RUNTIME_UNAVAILABLE", str(exc)) from exc
        ws_scheme = "wss" if context.config.tls_cert else "ws"
        socket_base = (
            f"{ws_scheme}://{context.config.public_url_host}:{context.config.public_port}"
            f"/api/v1/sessions/{session.id}"
        )
        return {
            "sessionId": session.id,
            "controlUrl": f"{socket_base}/control",
            "videoUrl": f"{socket_base}/video",
        }

    @app.delete("/api/v1/sessions/{session_id}", status_code=204)
    async def delete_session(session_id: str, request: Request) -> Response:
        client = _bearer(request, context.auth)
        if not await context.sessions.end(session_id, client.client_id):
            raise _json_error(404, "SESSION_NOT_FOUND", "session not found")
        return Response(status_code=204)

    @app.websocket("/api/v1/sessions/{session_id}/control")
    async def control(websocket: WebSocket, session_id: str) -> None:
        try:
            client = context.auth.authenticate_token(_websocket_bearer(websocket))
        except AuthenticationError:
            await websocket.close(code=4401, reason="authentication failed")
            return
        session = context.sessions.get(session_id, client.client_id)
        if not session:
            await websocket.close(code=4404, reason="session not found")
            return
        await websocket.accept()
        await _run_control_socket(websocket, session)

    @app.websocket("/api/v1/sessions/{session_id}/video")
    async def video(websocket: WebSocket, session_id: str) -> None:
        try:
            client = context.auth.authenticate_token(_websocket_bearer(websocket))
        except AuthenticationError:
            await websocket.close(code=4401, reason="authentication failed")
            return
        session = context.sessions.get(session_id, client.client_id)
        if not session:
            await websocket.close(code=4404, reason="session not found")
            return
        await websocket.accept()
        await _run_video_socket(websocket, session)

    return app


def create_admin_app(context: ServiceContext) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await context.acquire()
        try:
            yield
        finally:
            await context.release()

    app = FastAPI(title="CloudPhone Local Management", docs_url=None, redoc_url=None, lifespan=lifespan)
    auth = AdminSessionAuth(context.config.admin_password_hash, context.config.server_secret)
    static_dir = Path(__file__).with_name("static")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.middleware("http")
    async def admin_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
        return response

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.post("/api/admin/login")
    async def login(request: Request) -> JSONResponse:
        body = await _body(request, 4096)
        token, csrf = auth.login(str(body.get("password", "")))
        response = JSONResponse({"csrfToken": csrf, "expiresInSeconds": 3600})
        response.set_cookie(auth.COOKIE, token, max_age=3600, httponly=True, samesite="strict", secure=False, path="/")
        return response

    @app.post("/api/admin/logout", status_code=204)
    async def logout(request: Request) -> Response:
        auth.authenticate(request, True)
        response = Response(status_code=204)
        response.delete_cookie(auth.COOKIE, path="/")
        return response

    @app.get("/api/admin/status")
    async def status(request: Request) -> dict[str, object]:
        auth.authenticate(request, False)
        binding = context.db.get_client_binding()
        session = context.sessions.current
        return {
            "clientBound": bool(binding),
            "boundClientId": str(binding["client_id"]) if binding else "",
            "activeSession": asdict(session.description) if session else None,
            "streamMetrics": session.stream_metrics if session else None,
            "publicAddress": context.config.public_base_url,
            "streamTransport": "TLS WebSocket",
        }

    @app.get("/api/admin/devices")
    async def admin_devices(request: Request) -> dict[str, object]:
        auth.authenticate(request, False)
        return {"devices": [device.public_dict() for device in context.db.list_devices()]}

    @app.put("/api/admin/devices/{device_id}")
    async def update_device(device_id: str, request: Request) -> dict[str, object]:
        auth.authenticate(request, True)
        body = await _body(request, 4096)
        alias = body.get("alias") if isinstance(body.get("alias"), str) else None
        authorized = body.get("authorized") if isinstance(body.get("authorized"), bool) else None
        device = context.db.update_device(device_id, alias=alias, authorized=authorized)
        if not device:
            raise _json_error(404, "DEVICE_NOT_FOUND", "device not found")
        if authorized is False:
            await context.sessions.revoke_device(device_id)
        return {"device": device.public_dict()}

    @app.post("/api/admin/devices/refresh")
    async def refresh_devices(request: Request) -> dict[str, object]:
        auth.authenticate(request, True)
        try:
            await context.tracker.refresh_once()
        except (AdbError, OSError) as exc:
            raise _json_error(503, "ADB_UNAVAILABLE", str(exc)) from exc
        return {"devices": [device.public_dict() for device in context.db.list_devices()]}

    @app.post("/api/admin/client/reset", status_code=204)
    async def reset_client(request: Request) -> Response:
        auth.authenticate(request, True)
        await context.sessions.shutdown()
        context.db.reset_client_binding()
        return Response(status_code=204)

    return app
