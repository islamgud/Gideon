"""
server.py — точка входа GIDEON

Запускает три параллельные задачи:
    1. WebSocket-сервер   ws://localhost:8765  (связь с визуалом)
    2. Голосовой цикл     микрофон → распознавание → core
    3. CLI                ввод команд из терминала

Запуск:
    python server.py

Флаги:
    python server.py --no-voice   # только CLI + WebSocket, без микрофона
    python server.py --no-tts     # без синтеза речи
"""

import asyncio
import json
import logging
import sys
import argparse
import websockets
from websockets.server import WebSocketServerProtocol

from core import GideonCore

# ── логирование ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-18s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("gideon.server")

HOST = "localhost"
PORT = 8765

# ── активные WebSocket-соединения ─────────────────────────────
_clients: set[WebSocketServerProtocol] = set()


async def broadcast(message: str) -> None:
    """Отправить JSON всем подключённым клиентам."""
    if not _clients:
        return
    await asyncio.gather(
        *[client.send(message) for client in _clients],
        return_exceptions=True,
    )


# ══════════════════════════════════════════════════════════════
#  WebSocket — обработка соединения
# ══════════════════════════════════════════════════════════════
async def ws_handler(ws: WebSocketServerProtocol) -> None:
    _clients.add(ws)
    addr = ws.remote_address
    logger.info("Клиент: %s  (всего: %d)", addr, len(_clients))

    # Сразу отправить текущее состояние
    await ws.send(json.dumps({"state": gideon.get_state()}))

    try:
        async for raw in ws:
            try:
                data    = json.loads(raw)
                command = data.get("command", "").strip()
            except (json.JSONDecodeError, AttributeError):
                command = str(raw).strip()

            if command:
                logger.info("WS ← %s: %r", addr, command)
                result = await gideon.process_command(command)
                await ws.send(json.dumps(result))

    except websockets.exceptions.ConnectionClosedOK:
        pass
    except websockets.exceptions.ConnectionClosedError as e:
        logger.warning("WS разрыв: %s", e)
    finally:
        _clients.discard(ws)
        logger.info("Клиент отключён: %s  (всего: %d)", addr, len(_clients))


# ══════════════════════════════════════════════════════════════
#  Голосовой цикл
# ══════════════════════════════════════════════════════════════
async def voice_loop() -> None:
    """
    Непрерывно слушает микрофон.
    Распознанный текст передаётся в GideonCore.
    """
    try:
        from voice import wake_word_loop
    except ImportError:
        logger.error("voice.py не найден")
        return

    async def on_wake(text: str):
        """Услышали wake-слово."""
        logger.info("Wake-word: %r", text)
        await gideon.process_command(text)

    async def on_command(text: str):
        """Услышали обычную команду."""
        logger.info("Голос: %r", text)
        await gideon.process_command(text)

    logger.info("Голосовой цикл запущен")
    await wake_word_loop(on_wake, on_command)


# ══════════════════════════════════════════════════════════════
#  CLI — ввод команд из терминала
# ══════════════════════════════════════════════════════════════
async def cli_loop() -> None:
    loop = asyncio.get_event_loop()

    print()
    print("  ╔══════════════════════════════════════╗")
    print("  ║         G I D E O N   core           ║")
    print("  ╠══════════════════════════════════════╣")
    print("  ║  Команды:                            ║")
    print("  ║    гидеон      — пробудить           ║")
    print("  ║    подумай     — think               ║")
    print("  ║    слушай      — listen              ║")
    print("  ║    стоп        — idle                ║")
    print("  ║    спать       — sleep               ║")
    print("  ║    статус      — статус              ║")
    print("  ║    запомни X=Y — сохранить           ║")
    print("  ║    вспомни     — показать память     ║")
    print("  ║    quit        — выход               ║")
    print("  ╚══════════════════════════════════════╝")
    print()

    while True:
        try:
            line = await loop.run_in_executor(None, sys.stdin.readline)
        except (EOFError, KeyboardInterrupt):
            break

        command = line.strip()
        if not command:
            continue

        if command.lower() in ("quit", "exit", "q", "выход"):
            logger.info("Завершение")
            loop.stop()
            break

        result = await gideon.process_command(command)
        print(f"  → {result}\n")


# ══════════════════════════════════════════════════════════════
#  ТОЧКА ВХОДА
# ══════════════════════════════════════════════════════════════
def parse_args():
    parser = argparse.ArgumentParser(description="GIDEON Assistant Core")
    parser.add_argument("--no-voice", action="store_true",
                        help="Отключить голосовой ввод (микрофон)")
    parser.add_argument("--no-tts",   action="store_true",
                        help="Отключить синтез речи")
    parser.add_argument("--port",     type=int, default=PORT,
                        help=f"WebSocket порт (по умолчанию {PORT})")
    return parser.parse_args()


gideon = GideonCore()


async def main() -> None:
    args = parse_args()

    gideon.set_broadcast(broadcast)
    if args.no_tts:
        gideon.set_tts(False)
        logger.info("TTS отключён (--no-tts)")

    # WebSocket сервер
    server = await websockets.serve(ws_handler, HOST, args.port)
    logger.info("WebSocket: ws://%s:%d", HOST, args.port)

    # Задачи
    tasks = [
        asyncio.create_task(server.wait_closed()),
        asyncio.create_task(cli_loop()),
    ]

    if not args.no_voice:
        tasks.append(asyncio.create_task(voice_loop()))
    else:
        logger.info("Голосовой ввод отключён (--no-voice)")

    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановлен (Ctrl+C)")
