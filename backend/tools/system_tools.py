"""
backend/tools/system_tools.py
═══════════════════════════════════════════════════════════════════════════════
Системные инструменты GIDEON.

Все функции возвращают dict:
  {"success": True,  "message": "..."}
  {"success": False, "message": "..."}

@tool-обёртки регистрируют их в реестре под именами, которые
понимает AIBrain (claude tool_use).

Инструменты:
  open_app        — открыть приложение (notepad / browser / calculator)
  open_url        — открыть конкретный URL
  shutdown_pc     — выключить компьютер
  restart_pc      — перезагрузить
  lock_pc         — заблокировать экран
  get_system_info — информация о системе (ОС, CPU, RAM, диск, время)
  set_volume      — управление громкостью (Windows / macOS / Linux)
  take_screenshot — снимок экрана → сохранить на рабочий стол
"""

import asyncio
import datetime
import logging
import os
import platform
import subprocess
import webbrowser
from pathlib import Path

from .registry import tool

logger = logging.getLogger("gideon.tools.system")

_OS = platform.system()   # "Windows" | "Darwin" | "Linux"


# ═══════════════════════════════════════════════════════════════════════════
#  ВСПОМОГАТЕЛЬНЫЕ УТИЛИТЫ
# ═══════════════════════════════════════════════════════════════════════════

def _ok(message: str) -> dict:
    return {"success": True, "message": message}

def _err(message: str) -> dict:
    return {"success": False, "message": message}

def _run(cmd: list[str], *, shell: bool = False) -> dict:
    """Запустить процесс, вернуть стандартный dict."""
    try:
        subprocess.Popen(cmd, shell=shell)
        return _ok(f"Выполнено")
    except FileNotFoundError:
        return _err(f"Не найдено: {cmd[0]}")
    except Exception as exc:
        return _err(str(exc))

def _to_tool_result(result: dict) -> dict:
    """Конвертировать {success, message} → {response} | {error}."""
    if result.get("success"):
        return {"response": result["message"]}
    return {"error": result["message"]}

async def _async(fn, *args) -> dict:
    """Запустить синхронную функцию в executor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, fn, *args)


# ═══════════════════════════════════════════════════════════════════════════
#  1. ОТКРЫТИЕ ПРИЛОЖЕНИЙ
# ═══════════════════════════════════════════════════════════════════════════

_APP_MAP: dict[str, dict[str, list[str]]] = {
    "notepad": {
        "Windows": ["notepad"],
        "Darwin":  ["open", "-a", "TextEdit"],
        "Linux":   ["gedit"],
    },
    "browser": {
        "Windows": ["cmd", "/c", "start", ""],   # откроет браузер по умолчанию
        "Darwin":  ["open", "-a", "Safari"],
        "Linux":   ["xdg-open", "https://google.com"],
    },
    "calculator": {
        "Windows": ["calc"],
        "Darwin":  ["open", "-a", "Calculator"],
        "Linux":   ["gnome-calculator"],
    },
}

_APP_NAMES = {"notepad": "Блокнот", "browser": "Браузер", "calculator": "Калькулятор"}


def open_notepad() -> dict:
    cmd = _APP_MAP["notepad"].get(_OS)
    return _run(cmd) if cmd else _err(f"Блокнот не поддерживается на {_OS}")

def open_browser() -> dict:
    cmd = _APP_MAP["browser"].get(_OS)
    if cmd:
        r = _run(cmd)
        if r["success"]:
            r["message"] = "Браузер открыт"
            return r
    # fallback — системный браузер
    try:
        webbrowser.open("https://google.com")
        return _ok("Браузер открыт")
    except Exception as exc:
        return _err(str(exc))

def open_calculator() -> dict:
    cmd = _APP_MAP["calculator"].get(_OS)
    if not cmd:
        return _err(f"Калькулятор не поддерживается на {_OS}")
    r = _run(cmd)
    if r["success"]:
        r["message"] = "Калькулятор открыт"
    return r


@tool("open_app")
async def _open_app_tool(args: dict) -> dict:
    app = args.get("app", "").lower().strip()
    fn_map = {"notepad": open_notepad, "browser": open_browser, "calculator": open_calculator}
    fn = fn_map.get(app)
    if not fn:
        return {"error": f"Неизвестное приложение: {app!r}"}
    result = await _async(fn)
    if result["success"]:
        result["message"] = f"{_APP_NAMES.get(app, app)} открыт"
    logger.info("open_app(%s): %s", app, result)
    return _to_tool_result(result)


# ═══════════════════════════════════════════════════════════════════════════
#  2. ОТКРЫТИЕ URL
# ═══════════════════════════════════════════════════════════════════════════

def open_url(url: str) -> dict:
    """Открыть URL в браузере по умолчанию."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        webbrowser.open(url)
        return _ok(f"Открываю {url}")
    except Exception as exc:
        return _err(str(exc))


@tool("open_url")
async def _open_url_tool(args: dict) -> dict:
    url = args.get("url", "").strip()
    if not url:
        return {"error": "URL не указан"}
    result = await _async(open_url, url)
    return _to_tool_result(result)


# ═══════════════════════════════════════════════════════════════════════════
#  3. УПРАВЛЕНИЕ ПИТАНИЕМ
# ═══════════════════════════════════════════════════════════════════════════

def shutdown_pc() -> dict:
    try:
        if _OS == "Windows":
            subprocess.Popen(["shutdown", "/s", "/t", "5"])
        elif _OS in ("Darwin", "Linux"):
            subprocess.Popen(["sudo", "shutdown", "-h", "+1"])
        else:
            return _err(f"Выключение не поддерживается на {_OS}")
        return _ok("Компьютер выключится через 5 секунд")
    except Exception as exc:
        return _err(str(exc))

def restart_pc() -> dict:
    try:
        if _OS == "Windows":
            subprocess.Popen(["shutdown", "/r", "/t", "5"])
        elif _OS in ("Darwin", "Linux"):
            subprocess.Popen(["sudo", "shutdown", "-r", "+1"])
        else:
            return _err(f"Перезагрузка не поддерживается на {_OS}")
        return _ok("Перезагрузка через 5 секунд")
    except Exception as exc:
        return _err(str(exc))

def lock_pc() -> dict:
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
            for locker in (
                ["gnome-screensaver-command", "-l"],
                ["loginctl", "lock-session"],
                ["xdg-screensaver", "lock"],
            ):
                try:
                    subprocess.Popen(locker)
                    return _ok("Экран заблокирован")
                except FileNotFoundError:
                    continue
            return _err("Не найден инструмент блокировки")
        else:
            return _err(f"Блокировка не поддерживается на {_OS}")
        return _ok("Экран заблокирован")
    except Exception as exc:
        return _err(str(exc))


@tool("shutdown_pc")
async def _shutdown_tool(_args: dict) -> dict:
    return _to_tool_result(await _async(shutdown_pc))

@tool("restart_pc")
async def _restart_tool(_args: dict) -> dict:
    return _to_tool_result(await _async(restart_pc))

@tool("lock_pc")
async def _lock_tool(_args: dict) -> dict:
    return _to_tool_result(await _async(lock_pc))


# ═══════════════════════════════════════════════════════════════════════════
#  4. ИНФОРМАЦИЯ О СИСТЕМЕ
# ═══════════════════════════════════════════════════════════════════════════

def get_system_info(info_type: str = "all") -> dict:
    """
    Собрать информацию о системе.
    info_type: "all" | "os" | "cpu" | "memory" | "disk" | "time"
    """
    parts: list[str] = []

    try:
        import shutil

        def _os_info() -> str:
            return (
                f"ОС: {platform.system()} {platform.release()} "
                f"({platform.machine()})"
            )

        def _cpu_info() -> str:
            cpu = platform.processor() or platform.machine()
            try:
                import psutil
                freq = psutil.cpu_freq()
                pct  = psutil.cpu_percent(interval=0.2)
                cores = psutil.cpu_count(logical=False)
                return f"CPU: {cpu}, {cores} ядер, {freq.current:.0f} МГц, загрузка {pct:.0f}%"
            except ImportError:
                return f"CPU: {cpu}"

        def _mem_info() -> str:
            try:
                import psutil
                m = psutil.virtual_memory()
                total = m.total // (1024 ** 2)
                avail = m.available // (1024 ** 2)
                return f"RAM: {total} МБ всего, {avail} МБ свободно ({m.percent:.0f}% занято)"
            except ImportError:
                return "RAM: psutil не установлен"

        def _disk_info() -> str:
            try:
                import psutil
                d = psutil.disk_usage("/")
                total = d.total // (1024 ** 3)
                free  = d.free  // (1024 ** 3)
                return f"Диск: {total} ГБ всего, {free} ГБ свободно ({d.percent:.0f}% занято)"
            except ImportError:
                total, used, free = shutil.disk_usage("/")
                return (
                    f"Диск: {total//(1024**3)} ГБ всего, "
                    f"{free//(1024**3)} ГБ свободно"
                )

        def _time_info() -> str:
            now = datetime.datetime.now()
            return f"Время: {now.strftime('%H:%M:%S')}, Дата: {now.strftime('%d.%m.%Y')}"

        collectors = {
            "os":     _os_info,
            "cpu":    _cpu_info,
            "memory": _mem_info,
            "disk":   _disk_info,
            "time":   _time_info,
        }

        if info_type == "all":
            for fn in collectors.values():
                try:
                    parts.append(fn())
                except Exception as e:
                    parts.append(f"(ошибка: {e})")
        else:
            fn = collectors.get(info_type)
            if fn:
                parts.append(fn())
            else:
                return _err(f"Неизвестный тип информации: {info_type!r}")

        return _ok(" | ".join(parts))

    except Exception as exc:
        return _err(f"Ошибка сбора информации: {exc}")


@tool("get_system_info")
async def _sysinfo_tool(args: dict) -> dict:
    info_type = args.get("info_type", "all")
    result = await _async(get_system_info, info_type)
    return _to_tool_result(result)


# ═══════════════════════════════════════════════════════════════════════════
#  5. УПРАВЛЕНИЕ ГРОМКОСТЬЮ
# ═══════════════════════════════════════════════════════════════════════════

def set_volume(action: str, level: int | None = None,
               delta: int | None = None) -> dict:
    """
    Управление громкостью.
    action: "mute" | "unmute" | "up" | "down" | "set"
    level:  0-100 (для action="set")
    delta:  шаг изменения в % (для action="up"/"down")
    """
    try:
        if _OS == "Windows":
            return _volume_windows(action, level, delta)
        elif _OS == "Darwin":
            return _volume_macos(action, level)
        elif _OS == "Linux":
            return _volume_linux(action, level)
        else:
            return _err(f"Управление звуком не поддерживается на {_OS}")
    except Exception as exc:
        return _err(str(exc))


def _volume_windows(action: str, level: int | None,
                    delta: int | None = None) -> dict:
    """
    Управление громкостью Windows.
    Приоритет: pycaw (точный) → nircmd → PowerShell keypress (fallback)
    """
    # ── Метод 1: pycaw — самый точный ───────────────────────────────────
    try:
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))

        if action == "mute":
            volume.SetMute(1, None)
            return _ok("Звук отключён")

        if action == "unmute":
            volume.SetMute(0, None)
            return _ok("Звук включён")

        # Текущий уровень 0.0-1.0
        current = round(volume.GetMasterVolumeLevelScalar() * 100)

        if action == "set" and level is not None:
            volume.SetMasterVolumeLevelScalar(level / 100, None)
            return _ok(f"Громкость: {level}%")

        if action == "up":
            step = delta if delta is not None else 10
            new_level = min(100, current + step)
            volume.SetMasterVolumeLevelScalar(new_level / 100, None)
            return _ok(f"Громкость: {new_level}%")

        if action == "down":
            step = delta if delta is not None else 10
            new_level = max(0, current - step)
            volume.SetMasterVolumeLevelScalar(new_level / 100, None)
            return _ok(f"Громкость: {new_level}%")

        return _err(f"Неизвестное действие: {action!r}")

    except ImportError:
        pass  # pycaw не установлен — идём дальше
    except Exception as exc:
        logger.warning("pycaw ошибка: %s", exc)

    # ── Метод 2: nircmd (если установлен) ───────────────────────────────
    try:
        if action == "mute":
            subprocess.run(["nircmd", "mutesysvolume", "1"], check=True,
                          capture_output=True)
            return _ok("Звук отключён")
        if action == "unmute":
            subprocess.run(["nircmd", "mutesysvolume", "0"], check=True,
                          capture_output=True)
            return _ok("Звук включён")
        if action == "set" and level is not None:
            val = int(65535 * level / 100)
            subprocess.run(["nircmd", "setsysvolume", str(val)], check=True,
                          capture_output=True)
            return _ok(f"Громкость: {level}%")
        if action in ("up", "down"):
            step = delta if delta is not None else 10
            val = int(65535 * step / 100)
            cmd = "changesysvolume"
            amount = str(val) if action == "up" else str(-val)
            subprocess.run(["nircmd", cmd, amount], check=True,
                          capture_output=True)
            label = "увеличена" if action == "up" else "уменьшена"
            return _ok(f"Громкость {label} на {step}%")
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    # ── Метод 3: PowerShell keypress fallback ────────────────────────────
    key_map = {
        "mute":   173,  # VK_VOLUME_MUTE
        "unmute": 173,
        "up":     175,  # VK_VOLUME_UP
        "down":   174,  # VK_VOLUME_DOWN
    }
    key = key_map.get(action)
    if key is None:
        return _err(f"Неизвестное действие: {action!r}")

    # Количество нажатий = delta / 2 (каждое нажатие = ~2%)
    presses = max(1, (delta or 10) // 2) if action in ("up", "down") else 1
    script = (
        f"$wsh = New-Object -ComObject WScript.Shell; "
        f"for($i=0; $i -lt {presses}; $i++) "
        f"{{ $wsh.SendKeys([char]{key}) }}"
    )
    subprocess.Popen(
        ["powershell", "-NonInteractive", "-WindowStyle", "Hidden",
         "-Command", script],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    labels = {
        "mute": "Звук отключён", "unmute": "Звук включён",
        "up": f"Громче на ~{(delta or 10)}%",
        "down": f"Тише на ~{(delta or 10)}%",
    }
    return _ok(labels.get(action, "Готово"))


def _volume_macos(action: str, level: int | None) -> dict:
    scripts = {
        "mute":   "set volume output muted true",
        "unmute": "set volume output muted false",
        "up":     "set volume output volume (output volume of (get volume settings) + 10)",
        "down":   "set volume output volume (output volume of (get volume settings) - 10)",
    }
    if action == "set" and level is not None:
        script = f"set volume output volume {level}"
    elif action in scripts:
        script = scripts[action]
    else:
        return _err(f"Неизвестное действие: {action!r}")
    subprocess.Popen(["osascript", "-e", script])
    return _ok({"mute": "Звук отключён", "unmute": "Звук включён",
                "up": "Громче", "down": "Тише", "set": f"Громкость: {level}%"}.get(action, "Готово"))


def _volume_linux(action: str, level: int | None) -> dict:
    cmds = {
        "mute":   ["amixer", "-q", "sset", "Master", "mute"],
        "unmute": ["amixer", "-q", "sset", "Master", "unmute"],
        "up":     ["amixer", "-q", "sset", "Master", "5%+"],
        "down":   ["amixer", "-q", "sset", "Master", "5%-"],
    }
    if action == "set" and level is not None:
        cmd = ["amixer", "-q", "sset", "Master", f"{level}%"]
    elif action in cmds:
        cmd = cmds[action]
    else:
        return _err(f"Неизвестное действие: {action!r}")
    return _run(cmd)


@tool("set_volume")
async def _volume_tool(args: dict) -> dict:
    action = args.get("action", "")
    level  = args.get("level")
    delta  = args.get("delta")

    if not action:
        return {"error": "action не указан"}

    result = await _async(set_volume, action, level, delta)
    return _to_tool_result(result)


# ═══════════════════════════════════════════════════════════════════════════
#  6. СКРИНШОТ
# ═══════════════════════════════════════════════════════════════════════════

def take_screenshot() -> dict:
    """Сделать снимок экрана и сохранить на рабочий стол."""
    try:
        desktop = Path.home() / "Desktop"
        desktop.mkdir(exist_ok=True)
        ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = desktop / f"gideon_screenshot_{ts}.png"

        # Пробуем PIL/Pillow
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab()
            img.save(str(path))
            return _ok(f"Скриншот сохранён: {path.name}")
        except ImportError:
            pass

        # Fallback: системные инструменты
        if _OS == "Windows":
            # PowerShell + .NET
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$bmp = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
                "$img = New-Object System.Drawing.Bitmap($bmp.Width, $bmp.Height); "
                "$g = [System.Drawing.Graphics]::FromImage($img); "
                f"$g.CopyFromScreen(0,0,0,0,$bmp.Size); "
                f"$img.Save('{path}')"
            )
            subprocess.run(
                ["powershell", "-NonInteractive", "-Command", ps],
                check=True, capture_output=True
            )
            return _ok(f"Скриншот сохранён: {path.name}")

        elif _OS == "Darwin":
            subprocess.run(["screencapture", str(path)], check=True)
            return _ok(f"Скриншот сохранён: {path.name}")

        elif _OS == "Linux":
            for cmd in (
                ["gnome-screenshot", "-f", str(path)],
                ["scrot", str(path)],
                ["import", "-window", "root", str(path)],  # ImageMagick
            ):
                try:
                    subprocess.run(cmd, check=True, capture_output=True)
                    return _ok(f"Скриншот сохранён: {path.name}")
                except (FileNotFoundError, subprocess.CalledProcessError):
                    continue
            return _err("Не найден инструмент для скриншота (установи scrot или gnome-screenshot)")

        return _err(f"Скриншот не поддерживается на {_OS}")

    except Exception as exc:
        return _err(f"Ошибка скриншота: {exc}")


@tool("take_screenshot")
async def _screenshot_tool(_args: dict) -> dict:
    result = await _async(take_screenshot)
    return _to_tool_result(result)


# ═══════════════════════════════════════════════════════════════════════════
#  7. СИСТЕМНЫЕ ПРИЛОЖЕНИЯ WINDOWS (проводник, диспетчер задач и т.д.)
# ═══════════════════════════════════════════════════════════════════════════

_SYSTEM_APPS: dict[str, dict] = {
    "explorer": {"cmd": ["explorer"],          "name": "Проводник"},
    "taskmgr":  {"cmd": ["taskmgr"],           "name": "Диспетчер задач"},
    "control":  {"cmd": ["control"],           "name": "Панель управления"},
    "cmd":      {"cmd": ["cmd"],               "name": "Командная строка"},
    "regedit":  {"cmd": ["regedit"],           "name": "Редактор реестра"},
    "mspaint":  {"cmd": ["mspaint"],           "name": "Paint"},
    "wordpad":  {"cmd": ["wordpad"],           "name": "WordPad"},
    "magnify":  {"cmd": ["magnify"],           "name": "Лупа"},
    "msconfig": {"cmd": ["msconfig"],          "name": "Конфигурация системы"},
    "devmgmt":  {"cmd": ["devmgmt.msc"],       "name": "Диспетчер устройств"},
    "eventvwr": {"cmd": ["eventvwr"],          "name": "Просмотр событий"},
    "perfmon":  {"cmd": ["perfmon"],           "name": "Монитор ресурсов"},
    "powershell": {"cmd": ["powershell"],        "name": "PowerShell"},
    "calendar": {
        "cmd": ["explorer", "shell:appsFolder\\Microsoft.WindowsAlarms_8wekyb3d8bbwe!App"],
        "name": "Календарь",
    },
    "downloads": {
        "cmd": ["explorer", os.path.join(os.path.expanduser("~"), "Downloads")],
        "name": "Загрузки",
    },
}

def open_system_app(app: str) -> dict:
    """Открыть системное приложение Windows по ключу."""
    entry = _SYSTEM_APPS.get(app.lower().strip())
    if not entry:
        return _err(f"Неизвестное системное приложение: {app!r}")
    if _OS != "Windows":
        return _err(f"{entry['name']} доступен только на Windows")
    result = _run(entry["cmd"])
    if result["success"]:
        result["message"] = f"{entry['name']} открыт"
    return result


@tool("open_system_app")
async def _open_system_app_tool(args: dict) -> dict:
    app = args.get("app", "").strip()
    if not app:
        return {"error": "app не указан"}
    result = await _async(open_system_app, app)
    return _to_tool_result(result)
