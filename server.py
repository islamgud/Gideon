"""
server.py — точка входа GIDEON
"""

import asyncio
import json
import logging
import sys
import argparse
import websockets
from websockets.server import WebSocketServerProtocol

from core import GideonCore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-18s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("gideon.server")

HOST = "localhost"
PORT = 8765

_clients: set[WebSocketServerProtocol] = set()


async def broadcast(message: str) -> None:
    """Отправить JSON всем подключённым клиентам (snapshot — без гонок)."""
    targets = list(_clients)  # FIX: snapshot, чтобы изменение множества во время gather не ломало итерацию
    if not targets:
        return
    await asyncio.gather(
        *[client.send(message) for client in targets],
        return_exceptions=True,
    )


# ══════════════════════════════════════════════════════════════
#  WebSocket
# ══════════════════════════════════════════════════════════════
async def ws_handler(ws: WebSocketServerProtocol, gideon: "GideonCore") -> None:
    _clients.add(ws)
    addr = ws.remote_address
    logger.info("Клиент: %s  (всего: %d)", addr, len(_clients))

    await ws.send(json.dumps({"state": gideon.get_state()}))

    try:
        async for raw in ws:
            try:
                data = json.loads(raw)
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
async def voice_loop(gideon: "GideonCore") -> None:
    try:
        from voice import wake_word_loop
    except ImportError:
        logger.error("voice.py не найден — голосовой цикл отключён")
        return

    async def on_wake(text: str) -> None:
        logger.info("Wake-word: %r", text)
        try:
            await gideon.process_command(text)
        except Exception:
            logger.exception("Ошибка в on_wake")  # FIX: не убиваем loop при сбое

    async def on_command(text: str) -> None:
        logger.info("Голос: %r", text)
        try:
            await gideon.process_command(text)
        except Exception:
            logger.exception("Ошибка в on_command")  # FIX: аналогично

    logger.info("Голосовой цикл запущен")
    await wake_word_loop(on_wake, on_command)


# ══════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════
async def cli_loop(gideon: "GideonCore", shutdown: asyncio.Event) -> None:
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

    while not shutdown.is_set():
        try:
            # FIX: читаем с таймаутом — не виснем на readline при отмене задачи
            line = await asyncio.wait_for(
                loop.run_in_executor(None, sys.stdin.readline),
                timeout=1.0,
            )
        except asyncio.TimeoutError:
            continue
        except (EOFError, KeyboardInterrupt):
            break

        command = line.strip()
        if not command:
            continue

        if command.lower() in ("quit", "exit", "q", "выход"):
            logger.info("Завершение по команде пользователя")
            shutdown.set()  # FIX: сигнализируем main о выходе вместо loop.stop()
            break

        result = await gideon.process_command(command)
        print(f"  → {result}\n")


# ══════════════════════════════════════════════════════════════
#  ТОЧКА ВХОДА
# ══════════════════════════════════════════════════════════════
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GIDEON Assistant Core")
    parser.add_argument("--no-voice", action="store_true",
                        help="Отключить голосовой ввод (микрофон)")
    parser.add_argument("--no-tts", action="store_true",
                        help="Отключить синтез речи")
    parser.add_argument("--port", type=int, default=PORT,
                        help=f"WebSocket порт (по умолчанию {PORT})")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    # FIX: создаём после парсинга — args доступны для конструктора если нужно
    gideon = GideonCore()
    gideon.set_broadcast(broadcast)

    if args.no_tts:
        gideon.set_tts(False)
        logger.info("TTS отключён (--no-tts)")

    shutdown = asyncio.Event()

    # Передаём gideon явно, а не через глобал
    server = await websockets.serve(
        lambda ws: ws_handler(ws, gideon),
        HOST,
        args.port,
    )
    logger.info("WebSocket: ws://%s:%d", HOST, args.port)

    tasks: list[asyncio.Task] = [
        asyncio.create_task(cli_loop(gideon, shutdown), name="cli"),
    ]

    if not args.no_voice:
        tasks.append(asyncio.create_task(voice_loop(gideon), name="voice"))
    else:
        logger.info("Голосовой ввод отключён (--no-voice)")

    # FIX: ждём сигнала завершения, а не просто gather задач
    await shutdown.wait()

    logger.info("Остановка задач...")
    for task in tasks:
        task.cancel()

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # FIX: логируем неожиданные исключения вместо молчаливого проглатывания
    for task, result in zip(tasks, results):
        if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
            logger.error("Задача '%s' завершилась с ошибкой: %s", task.get_name(), result)

    server.close()
    await server.wait_closed()
    logger.info("Сервер остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановлен (Ctrl+C)")
