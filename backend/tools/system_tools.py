"""
backend/tools/system_tools.py
Системные инструменты — открытие приложений и управление ОС.

Каждая функция возвращает dict:
  {"success": True,  "message": "..."}   — успех
  {"success": False, "message": "..."}   — ошибка

Инструменты, зарегистрированные через @tool("open_app"), сохраняют
совместимость с orchestrator.py.  Дополнительные инструменты
(shutdown_pc, restart_pc, lock_pc) регистрируются отдельно.
"""

import asyncio
import logging
import os
import platform
import subprocess
import webbrowser

from .registry import tool

logger = logging.getLogger("gideon.tools.system")

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

# ─── Команды запуска приложений ────────────────────────────────────────────
_APP_COMMANDS: dict[str, dict[str, list[str]]] = {
    "notepad": {
        "Windows": ["notepad"],
        "Darwin":  ["open", "-a", "TextEdit"],
        "Linux":   ["gedit"],
    },
    "browser": {
        "Windows": ["cmd", "/c", "start", "chrome"],
        "Darwin":  ["open", "-a", "Google Chrome"],
        "Linux":   ["xdg-open", "https://google.com"],
    },
    "calculator": {
        "Windows": ["calc"],
        "Darwin":  ["open", "-a", "Calculator"],
        "Linux":   ["gnome-calculator"],
    },
}

# ─── Человекочитаемые имена приложений ─────────────────────────────────────
_APP_NAMES: dict[str, str] = {
    "notepad":    "Блокнот",
    "browser":    "Браузер",
    "calculator": "Калькулятор",
}


# ═══════════════════════════════════════════════════════════════════════════
#  Вспомогательные функции (используются CommandProcessor напрямую)
# ═══════════════════════════════════════════════════════════════════════════

def _run(cmd: list[str]) -> dict:
    """Запустить процесс; вернуть стандартизованный dict."""
    try:
        subprocess.Popen(cmd, shell=False)
        return {"success": True, "message": f"Выполнено: {cmd[0]}"}
    except FileNotFoundError:
        return {"success": False, "message": f"Не найдено: {cmd[0]}"}
    except Exception as exc:
        return {"success": False, "message": str(exc)}


def open_notepad() -> dict:
    """Открыть текстовый редактор."""
    cmd = _APP_COMMANDS["notepad"].get(_OS, [])
    if not cmd:
        return {"success": False, "message": f"Блокнот не поддерживается на {_OS}"}
    result = _run(cmd)
    if result["success"]:
        result["message"] = "Блокнот открыт"
    return result


def open_browser() -> dict:
    """Открыть браузер (fallback — webbrowser)."""
    cmd = _APP_COMMANDS["browser"].get(_OS, [])
    if cmd:
        result = _run(cmd)
        if result["success"]:
            result["message"] = "Браузер открыт"
            return result
    # Fallback: стандартный браузер системы
    try:
        webbrowser.open("https://google.com")
        return {"success": True, "message": "Браузер открыт"}
    except Exception as exc:
        return {"success": False, "message": str(exc)}


def open_calculator() -> dict:
    """Открыть калькулятор."""
    cmd = _APP_COMMANDS["calculator"].get(_OS, [])
    if not cmd:
        return {"success": False, "message": f"Калькулятор не поддерживается на {_OS}"}
    result = _run(cmd)
    if result["success"]:
        result["message"] = "Калькулятор открыт"
    return result


def shutdown_pc() -> dict:
    """Выключить компьютер (через 5 секунд, чтобы успеть ответить)."""
    try:
        if _OS == "Windows":
            subprocess.Popen(["shutdown", "/s", "/t", "5"])
        elif _OS in ("Darwin", "Linux"):
            subprocess.Popen(["sudo", "shutdown", "-h", "+1"])
        else:
            return {"success": False, "message": f"Выключение не поддерживается на {_OS}"}
        return {"success": True, "message": "Компьютер выключится через 5 секунд"}
    except Exception as exc:
        return {"success": False, "message": str(exc)}


def restart_pc() -> dict:
    """Перезагрузить компьютер."""
    try:
        if _OS == "Windows":
            subprocess.Popen(["shutdown", "/r", "/t", "5"])
        elif _OS in ("Darwin", "Linux"):
            subprocess.Popen(["sudo", "shutdown", "-r", "+1"])
        else:
            return {"success": False, "message": f"Перезагрузка не поддерживается на {_OS}"}
        return {"success": True, "message": "Перезагрузка через 5 секунд"}
    except Exception as exc:
        return {"success": False, "message": str(exc)}


def lock_pc() -> dict:
    """Заблокировать рабочую станцию."""
    try:
        if _OS == "Windows":
            subprocess.Popen(["rundll32.exe", "user32.dll,LockWorkStation"])
        elif _OS == "Darwin":
            subprocess.Popen([
                "osascript", "-e",
                'tell application "System Events" to keystroke "q" '
                'using {command down, control down}'
            ])
        elif _OS == "Linux":
            # Пробуем несколько блокировщиков по порядку
            for locker in (["gnome-screensaver-command", "-l"],
                           ["loginctl", "lock-session"],
                           ["xdg-screensaver", "lock"]):
                try:
                    subprocess.Popen(locker)
                    return {"success": True, "message": "Экран заблокирован"}
                except FileNotFoundError:
                    continue
            return {"success": False, "message": "Не найден инструмент блокировки"}
        else:
            return {"success": False, "message": f"Блокировка не поддерживается на {_OS}"}
        return {"success": True, "message": "Экран заблокирован"}
    except Exception as exc:
        return {"success": False, "message": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
#  @tool — обёртки для реестра (async, возвращают "response" / "error")
# ═══════════════════════════════════════════════════════════════════════════

def _to_tool_result(result: dict) -> dict:
    """Конвертировать {success, message} → {response} | {error}."""
    if result.get("success"):
        return {"response": result["message"]}
    return {"error": result["message"]}


@tool("open_app")
async def open_app(args: dict) -> dict:
    """
    Открыть приложение по имени.
    args = {"app": "notepad" | "browser" | "calculator"}
    """
    app = args.get("app", "").lower().strip()

    dispatch = {
        "notepad":    open_notepad,
        "browser":    open_browser,
        "calculator": open_calculator,
    }

    fn = dispatch.get(app)
    if not fn:
        return {"error": f"Неизвестное приложение: {app!r}"}

    # Запускаем в executor, чтобы не блокировать event loop
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, fn)
    logger.info("open_app(%s): %s", app, result)
    return _to_tool_result(result)


@tool("shutdown_pc")
async def _shutdown_pc_tool(_args: dict) -> dict:
    loop = asyncio.get_event_loop()
    return _to_tool_result(await loop.run_in_executor(None, shutdown_pc))


@tool("restart_pc")
async def _restart_pc_tool(_args: dict) -> dict:
    loop = asyncio.get_event_loop()
    return _to_tool_result(await loop.run_in_executor(None, restart_pc))


@tool("lock_pc")
async def _lock_pc_tool(_args: dict) -> dict:
    loop = asyncio.get_event_loop()
    return _to_tool_result(await loop.run_in_executor(None, lock_pc))
