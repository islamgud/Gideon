"""
backend/ws_server.py
WebSocket сервер на :8765. Frontend подключается к нему.

Протокол (входящие сообщения):
  Старый формат (совместимость):
    {"command": "открой блокнот"}

  Новые форматы:
    {"type": "text_command",  "command": "открой блокнот"}
    {"type": "voice_command"}

Протокол (исходящие сообщения):
  {"state": "think" | "listen" | "speak" | "idle"}
  {"state": "speak", "status": "success", "response": "Блокнот открыт"}
  {"state": "speak", "status": "error",   "response": "Команда не распознана"}
  {"state": "think", "recognized": "открой блокнот"}   # после голосового ввода
  {"state": "listen", "status": "listening"}
"""

import asyncio
import json
import logging
import websockets

logger = logging.getLogger("gideon.ws")

HOST = "localhost"
PORT = 8765

_clients: set = set()


async def _broadcast(data: dict) -> None:
    """Отправить JSON всем подключённым клиентам."""
    if not _clients:
        return
    message = json.dumps(data, ensure_ascii=False)
    targets = list(_clients)
    await asyncio.gather(
        *[c.send(message) for c in targets],
        return_exceptions=True,
    )


async def _dispatch(data: dict, orchestrator) -> None:
    """
    Разобрать входящее сообщение и вызвать нужный метод оркестратора.

    Поддерживаемые форматы:
      {"command": "..."}                       — обратная совместимость
      {"type": "text_command", "command": "..."} — текстовая команда
      {"type": "voice_command"}                — голосовой ввод
    """
    msg_type = data.get("type", "")

    if msg_type == "voice_command":
        asyncio.create_task(orchestrator.process_voice_command())
        return

    if msg_type == "list_commands":
        asyncio.create_task(orchestrator.list_commands())
        return

    # text_command или старый формат command
    command = data.get("command", "").strip()
    if command:
        asyncio.create_task(orchestrator.process_text_command(command))


async def _handler(ws, orchestrator) -> None:
    """Обработка одного клиента."""
    _clients.add(ws)
    logger.info("Клиент подключён (всего: %d)", len(_clients))

    # Отправить текущее состояние сразу после подключения
    await ws.send(json.dumps({"state": orchestrator.state}))

    try:
        async for raw in ws:
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, AttributeError):
                # Поддержка plain-text команд (legacy)
                data = {"command": str(raw).strip()}

            await _dispatch(data, orchestrator)

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        _clients.discard(ws)
        logger.info("Клиент отключён (всего: %d)", len(_clients))


async def start(orchestrator) -> None:
    """Запустить WebSocket сервер."""
    orchestrator.set_broadcast(_broadcast)

    async with websockets.serve(
        lambda ws: _handler(ws, orchestrator),
        HOST,
        PORT,
    ):
        logger.info("WebSocket: ws://%s:%d", HOST, PORT)
        await asyncio.Future()
