"""WebSocket connection registry for broadcast telemetry."""

from __future__ import annotations

import json

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.fresh: set[WebSocket] = set()  # connected since the last broadcast; need the full state once

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.clients.add(websocket)
        self.fresh.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.clients.discard(websocket)
        self.fresh.discard(websocket)

    async def broadcast(self, payload: dict, full: dict | None = None) -> None:
        """Send `payload` to every client; clients that just connected get `full` instead.

        `payload` may leave out slow-changing fields (clients keep their last copy),
        so each message is serialised once here rather than once per client.
        """
        text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        full_text = text if full is None or full is payload else None
        failed: list[WebSocket] = []
        for client in self.clients.copy():
            try:
                if client in self.fresh:
                    if full_text is None:
                        full_text = json.dumps(full, separators=(",", ":"), ensure_ascii=False)
                    await client.send_text(full_text)
                    self.fresh.discard(client)
                else:
                    await client.send_text(text)
            except Exception:
                failed.append(client)
        for client in failed:
            self.disconnect(client)
