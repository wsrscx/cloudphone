from __future__ import annotations

import asyncio
from collections import deque
from typing import Awaitable, Callable

from .wire import EnvelopeMessage, ProtocolError, decode_envelope


class ControlOverloadError(ProtocolError):
    pass


EnvelopeHandler = Callable[[EnvelopeMessage], Awaitable[None]]
FatalHandler = Callable[[Exception], None]


class RemoteControlRouter:
    """Bounded, priority-aware ingress for interactive control messages.

    Reliable input commands are serialized separately from slower management
    operations. Motion and receiver statistics are replaceable state: keeping
    anything but their newest value only adds latency.
    """

    INPUT_TYPES = frozenset({"touch", "touch_moves", "navigation", "rotate"})
    SESSION_TYPES = frozenset({"quality_request", "receiver_stats", "ime_submit", "heartbeat"})
    CHANNEL_TYPES = {
        "input.v2": INPUT_TYPES - {"touch_moves"},
        "motion.v2": frozenset({"touch_moves"}),
        "control.v2": SESSION_TYPES,
        # Keep one-version compatibility so an installed old APK can still
        # reconnect while the new APK is being rolled out.
        "control.v1": (INPUT_TYPES - {"touch_moves"}) | SESSION_TYPES,
        "motion.v1": frozenset({"touch_moves"}),
    }

    def __init__(
        self,
        handler: EnvelopeHandler,
        on_fatal: FatalHandler,
        *,
        max_input_commands: int = 64,
        max_session_commands: int = 32,
    ):
        if max_input_commands < 1 or max_session_commands < 1:
            raise ValueError("control queue limits must be positive")
        self._handler = handler
        self._on_fatal = on_fatal
        self._max_input_commands = max_input_commands
        self._max_session_commands = max_session_commands
        self._input_commands: deque[EnvelopeMessage] = deque()
        self._session_commands: deque[EnvelopeMessage] = deque()
        self._latest_motion: EnvelopeMessage | None = None
        self._latest_stats: EnvelopeMessage | None = None
        self._input_ready = asyncio.Event()
        self._session_ready = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._last_sequence: dict[str, int] = {}
        self._closed = False

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("control router is closed")
        if self._tasks:
            return
        self._tasks = [
            asyncio.create_task(self._run_input(), name="remote-input"),
            asyncio.create_task(self._run_session(), name="remote-session-control"),
        ]

    def submit(self, data: bytes, channel: str) -> bool:
        if self._closed:
            return False
        if not self._tasks:
            raise RuntimeError("control router has not been started")
        allowed = self.CHANNEL_TYPES.get(channel)
        if allowed is None:
            raise ProtocolError(f"unsupported control channel: {channel}")

        envelope = decode_envelope(data)
        if envelope.payload_type not in allowed:
            raise ProtocolError(f"{channel} does not accept {envelope.payload_type}")
        if envelope.sequence <= 0:
            raise ProtocolError("control sequence must be positive")

        previous = self._last_sequence.get(channel, 0)
        if envelope.sequence <= previous:
            return False
        self._last_sequence[channel] = envelope.sequence

        if envelope.payload_type == "touch_moves":
            self._latest_motion = envelope
            self._input_ready.set()
            return True
        if envelope.payload_type in self.INPUT_TYPES:
            if len(self._input_commands) >= self._max_input_commands:
                raise ControlOverloadError("reliable input queue is overloaded")
            self._input_commands.append(envelope)
            self._input_ready.set()
            return True
        if envelope.payload_type == "receiver_stats":
            self._latest_stats = envelope
            self._session_ready.set()
            return True
        if envelope.payload_type == "heartbeat":
            return True
        if len(self._session_commands) >= self._max_session_commands:
            raise ControlOverloadError("session control queue is overloaded")
        self._session_commands.append(envelope)
        self._session_ready.set()
        return True

    async def _run_input(self) -> None:
        await self._run_lane(self._input_ready, self._take_input)

    async def _run_session(self) -> None:
        await self._run_lane(self._session_ready, self._take_session)

    async def _run_lane(
        self,
        ready: asyncio.Event,
        take: Callable[[], EnvelopeMessage | None],
    ) -> None:
        try:
            while True:
                await ready.wait()
                while True:
                    item = take()
                    if item is None:
                        ready.clear()
                        # submit() and this worker share an event loop, but keep
                        # the check explicit so a future threaded producer cannot
                        # lose a wake-up between take() and clear().
                        item = take()
                        if item is None:
                            break
                        ready.set()
                    await self._handler(item)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._closed:
                self._on_fatal(exc)

    def _take_input(self) -> EnvelopeMessage | None:
        if self._input_commands:
            return self._input_commands.popleft()
        motion, self._latest_motion = self._latest_motion, None
        return motion

    def _take_session(self) -> EnvelopeMessage | None:
        if self._session_commands:
            return self._session_commands.popleft()
        stats, self._latest_stats = self._latest_stats, None
        return stats

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._stop_workers()
        self._clear_pending()

    async def reset(self) -> None:
        """Start a clean control-transport generation and cancel old in-flight work."""
        if self._closed:
            return
        await self._stop_workers()
        self._clear_pending()
        self._last_sequence.clear()
        self._input_ready.clear()
        self._session_ready.clear()
        self.start()

    async def _stop_workers(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def _clear_pending(self) -> None:
        self._input_commands.clear()
        self._session_commands.clear()
        self._latest_motion = None
        self._latest_stats = None
