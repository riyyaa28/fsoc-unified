"""WebSocket connection registry for broadcast telemetry."""

from __future__ import annotations

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.clients.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.clients.discard(websocket)

    async def broadcast(self, payload: dict) -> None:
        failed: list[WebSocket] = []
        for client in self.clients.copy():
            try:
                await client.send_json(payload)
            except Exception:
                failed.append(client)
        for client in failed:
            self.disconnect(client)
