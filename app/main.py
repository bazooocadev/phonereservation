"""FastAPI メインアプリケーション"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from contextlib import asynccontextmanager
import logging
import os

from app.api import destinations, lines, operators, call_logs, dashboard, webhooks
from app.services import websocket_manager as ws_mgr
from app.services.redial_engine import engine as redial_engine
from app.config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Application started")
    yield
    await redial_engine.stop()
    logger.info("Application shutdown")


app = FastAPI(
    title="Redial System",
    version="1.0.0",
    lifespan=lifespan,
)

# ─── API ルーター登録 ─────────────────────────────────────
app.include_router(destinations.router)
app.include_router(lines.router)
app.include_router(operators.router)
app.include_router(call_logs.router)
app.include_router(dashboard.router)
app.include_router(webhooks.router)



# ─── WebSocket ────────────────────────────────────────────
@app.websocket("/ws/status")
async def ws_status(websocket: WebSocket):
    await ws_mgr.connect(websocket)
    try:
        while True:
            await websocket.receive_text()   # keep alive
    except WebSocketDisconnect:
        ws_mgr.disconnect(websocket)


# ─── ヘルスチェック ───────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "engine": redial_engine.is_running}


# ─── 静的ファイル / 管理画面 ──────────────────────────────
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    index_path = os.path.join(static_dir, "index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())
