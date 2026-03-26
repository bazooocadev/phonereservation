"""WebSocket接続マネージャー（リアルタイム状態配信）"""
from fastapi import WebSocket
from typing import Set, Any
import json
import logging

logger = logging.getLogger(__name__)


class WebSocketManager:
    def __init__(self):
        self.connections: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.add(ws)
        logger.info(f"WS connected. total={len(self.connections)}")

    def disconnect(self, ws: WebSocket):
        self.connections.discard(ws)
        logger.info(f"WS disconnected. total={len(self.connections)}")

    async def broadcast(self, data: Any):
        dead = set()
        for ws in self.connections:
            try:
                await ws.send_text(json.dumps(data, default=str))
            except Exception:
                dead.add(ws)
        self.connections -= dead


# シングルトン
websocket_manager = WebSocketManager()
