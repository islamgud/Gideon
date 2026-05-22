"""
main.py — точка входа GIDEON.

Запускает:
  1. asyncio loop в отдельном потоке: WebSocket server + Orchestrator
  2. pywebview в главном потоке: окно с frontend/index.html
"""

import asyncio
import logging
import os
import sys
import threading
import time

import webview

from backend.orchestrator import Orchestrator
from backend import ws_server
from backend.tools import registry  # noqa: F401 — регистрирует tools

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-22s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("gideon.main")


def _frontend_path() -> str:
    """Путь к frontend/index.html (работает и после PyInstaller)."""
    if hasattr(sys, "_MEIPASS"):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "frontend", "index.html")


def _run_backend(orchestrator: Orchestrator) -> None:
    """Точка входа фонового потока: asyncio + WebSocket."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(ws_server.start(orchestrator))
    except Exception:
        logger.exception("Backend упал")
    finally:
        loop.close()


def main() -> None:
    orchestrator = Orchestrator()

    backend_thread = threading.Thread(
        target=_run_backend,
        args=(orchestrator,),
        daemon=True,
        name="gideon-backend",
    )
    backend_thread.start()

    # Дать WebSocket-серверу подняться
    time.sleep(0.3)

    webview.create_window(
        title="GIDEON",
        url=_frontend_path(),
        width=1280,
        height=800,
        min_size=(800, 600),
        background_color="#091628",
        frameless=False,
        easy_drag=False,
        resizable=True,
    )

    logger.info("GIDEON запущен")
    webview.start(debug=False)
    logger.info("Окно закрыто, выход")


if __name__ == "__main__":
    main()
