"""WebSocket connection manager: fan-out broadcast to all connected clients."""

import asyncio
import json
from typing import Set

from fastapi import WebSocket


class WebSocketManager:
    def __init__(self):
        self._connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            self._connections.discard(ws)

    async def send_personal(self, ws: WebSocket, message: dict):
        try:
            await ws.send_text(json.dumps(message, default=str))
        except Exception:  # noqa: BLE001
            await self.disconnect(ws)

    async def broadcast(self, message: dict):
        if not self._connections:
            return
        payload = json.dumps(message, default=str)
        dead = []
        # snapshot to avoid mutation during iteration
        for ws in list(self._connections):
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)

    @property
    def count(self) -> int:
        return len(self._connections)


manager = WebSocketManager()
