"""
backend/ai_brain.py
═══════════════════════════════════════════════════════════════════════════════
ИИ-мозг GIDEON — Groq LLM → JSON команда → Python executor.

Архитектура:
  1. Пользователь говорит что угодно
  2. Groq LLM понимает смысл и возвращает JSON:
     {"action": "open_url", "value": "https://speedtest.net"}
  3. Python executor выполняет действие

Что хардкодим — только ДЕЙСТВИЯ (actions):
  open_url        — открыть любой сайт
  open_app        — открыть любое приложение
  open_folder     — открыть папку
  system_command  — системная команда (shutdown, restart, lock, volume, screenshot)
  get_info        — получить информацию (время, дата, характеристики)
  answer          — просто ответить текстом (если не команда)

Что НЕ хардкодим:
  — конкретные сайты
  — названия программ
  — вариации слов

Fallback:
  Если GROQ_API_KEY не задан — KeywordFallback.
"""

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("gideon.ai_brain")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from groq import Groq
    _GROQ_AVAILABLE = True
except ImportError:
    _GROQ_AVAILABLE = False
    logger.warning("groq не установлен. pip install groq")


# ═══════════════════════════════════════════════════════════════════════════
#  СИСТЕМНЫЙ ПРОМПТ
# ═══════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """Ты — ИИ-ядро голосового ассистента GIDEON для Windows.

Твоя ЕДИНСТВЕННАЯ задача — преобразовать команду пользователя в JSON.
Ты НИКОГДА не отвечаешь текстом. ТОЛЬКО JSON. Без markdown, без пояснений.

Доступные действия (actions):

1. open_url — открыть сайт в браузере
   {"action": "open_url", "value": "https://..."}

2. open_app — открыть приложение
   {"action": "open_app", "value": "название_программы"}
   Значения value: notepad, calculator, chrome, firefox, edge, discord,
   steam, spotify, vscode, word, excel, explorer, taskmgr, control,
   powershell, cmd, paint, winamp — или любое другое название exe

3. open_folder — открыть папку
   {"action": "open_folder", "value": "путь_или_название"}
   Значения value: downloads, documents, desktop, pictures, music,
   videos — или полный путь типа C:\\Users\\...

4. system_command — системная команда
   {"action": "system_command", "value": "команда", "param": "..."}
   Значения value:
     shutdown, restart, lock, screenshot,
     volume_up, volume_down, volume_mute, volume_unmute,
     volume_set (param = число 0-100),
     volume_delta_up (param = число), volume_delta_down (param = число)

5. get_info — получить информацию
   {"action": "get_info", "value": "тип"}
   Значения value: time, date, cpu, memory, disk, os, all

6. answer — ответить текстом (только если это не команда)
   {"action": "answer", "value": "текст ответа"}

Примеры:

"открой ютуб" → {"action": "open_url", "value": "https://youtube.com"}
"открой сайт спидтест" → {"action": "open_url", "value": "https://www.speedtest.net"}
"открой файрфокс" → {"action": "open_app", "value": "firefox"}
"открой дискорд" → {"action": "open_app", "value": "discord"}
"открой дс" → {"action": "open_app", "value": "discord"}
"открой загрузки" → {"action": "open_folder", "value": "downloads"}
"открой документы" → {"action": "open_folder", "value": "documents"}
"выключи комп" → {"action": "system_command", "value": "shutdown"}
"прибавь громкость на 15" → {"action": "system_command", "value": "volume_delta_up", "param": "15"}
"поставь громкость 50" → {"action": "system_command", "value": "volume_set", "param": "50"}
"который час" → {"action": "get_info", "value": "time"}
"привет" → {"action": "answer", "value": "Привет! Чем могу помочь?"}
"покажи настройки wifi" → {"action": "system_command", "value": "ncpa.cpl"}

Отвечай ТОЛЬКО валидным JSON. Никакого текста вокруг."""


# ═══════════════════════════════════════════════════════════════════════════
#  EXECUTOR — выполняет JSON команды
# ═══════════════════════════════════════════════════════════════════════════

class CommandExecutor:
    """
    Выполняет JSON команды от LLM.
    Хардкодим только действия — не конкретные значения.
    """

    async def execute(self, cmd: dict, registry_execute) -> dict:
        action = cmd.get("action", "")
        value  = cmd.get("value", "")
        param  = cmd.get("param", "")

        logger.info("Executor: action=%s value=%s param=%s", action, value, param)

        if action == "open_url":
            return await self._open_url(value, registry_execute)

        elif action == "open_app":
            return await self._open_app(value, registry_execute)

        elif action == "open_folder":
            return await self._open_folder(value, registry_execute)

        elif action == "system_command":
            return await self._system_command(value, param, registry_execute)

        elif action == "get_info":
            return await registry_execute("get_system_info", {"info_type": value or "all"})

        elif action == "answer":
            return {"response": value}

        else:
            return {"error": f"Неизвестное действие: {action}"}

    async def _open_url(self, url: str, registry_execute) -> dict:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        return await registry_execute("open_url", {"url": url})

    async def _open_app(self, app: str, registry_execute) -> dict:
        # Маппинг популярных имён → exe
        APP_MAP = {
            "chrome":    "chrome",
            "firefox":   "firefox",
            "edge":      "msedge",
            "discord":   "discord",
            "дискорд":   "discord",
            "дс":        "discord",
            "steam":     "steam",
            "spotify":   "spotify",
            "vscode":    "code",
            "vs code":   "code",
            "word":      "winword",
            "excel":     "excel",
            "explorer":  "explorer",
            "проводник": "explorer",
            "taskmgr":   "taskmgr",
            "диспетчер": "taskmgr",
            "control":   "control",
            "powershell":"powershell",
            "cmd":       "cmd",
            "paint":     "mspaint",
            "notepad":   "notepad",
            "блокнот":   "notepad",
            "calculator":"calc",
            "калькулятор":"calc",
            "telegram":  "telegram",
            "телеграм":  "telegram",
            "obs":       "obs64",
            "vlc":       "vlc",
            "zoom":      "zoom",
        }
        exe = APP_MAP.get(app.lower(), app)

        # Сначала пробуем через наш registry
        known = {"notepad": "notepad", "calculator": "calculator",
                 "calc": "calculator", "блокнот": "notepad"}
        if app.lower() in known or exe in known.values():
            app_key = known.get(app.lower(), known.get(exe, ""))
            if app_key:
                return await registry_execute("open_app", {"app": app_key})

        # Запускаем напрямую через subprocess
        import subprocess
        try:
            subprocess.Popen([exe], shell=True)
            return {"response": f"Открываю {app}"}
        except Exception as exc:
            return {"error": f"Не удалось открыть {app}: {exc}"}

    async def _open_folder(self, folder: str, registry_execute) -> dict:
        import subprocess, os
        FOLDER_MAP = {
            "downloads":  os.path.join(os.path.expanduser("~"), "Downloads"),
            "загрузки":   os.path.join(os.path.expanduser("~"), "Downloads"),
            "documents":  os.path.join(os.path.expanduser("~"), "Documents"),
            "документы":  os.path.join(os.path.expanduser("~"), "Documents"),
            "desktop":    os.path.join(os.path.expanduser("~"), "Desktop"),
            "рабочий стол": os.path.join(os.path.expanduser("~"), "Desktop"),
            "pictures":   os.path.join(os.path.expanduser("~"), "Pictures"),
            "картинки":   os.path.join(os.path.expanduser("~"), "Pictures"),
            "music":      os.path.join(os.path.expanduser("~"), "Music"),
            "музыка":     os.path.join(os.path.expanduser("~"), "Music"),
            "videos":     os.path.join(os.path.expanduser("~"), "Videos"),
            "видео":      os.path.join(os.path.expanduser("~"), "Videos"),
        }
        path = FOLDER_MAP.get(folder.lower(), folder)
        try:
            subprocess.Popen(["explorer", path])
            return {"response": f"Открываю {folder}"}
        except Exception as exc:
            return {"error": str(exc)}

    async def _system_command(self, value: str, param: str,
                               registry_execute) -> dict:
        SYSTEM_MAP = {
            "shutdown":          ("shutdown_pc",     {}),
            "restart":           ("restart_pc",      {}),
            "lock":              ("lock_pc",         {}),
            "screenshot":        ("take_screenshot", {}),
            "volume_up":         ("set_volume",      {"action": "up"}),
            "volume_down":       ("set_volume",      {"action": "down"}),
            "volume_mute":       ("set_volume",      {"action": "mute"}),
            "volume_unmute":     ("set_volume",      {"action": "unmute"}),
        }

        if value == "volume_set" and param:
            return await registry_execute(
                "set_volume", {"action": "set", "level": int(param)}
            )

        if value == "volume_delta_up" and param:
            return await registry_execute(
                "set_volume", {"action": "up", "delta": int(param)}
            )

        if value == "volume_delta_down" and param:
            return await registry_execute(
                "set_volume", {"action": "down", "delta": int(param)}
            )

        mapping = SYSTEM_MAP.get(value)
        if mapping:
            tool_name, args = mapping
            return await registry_execute(tool_name, args)

        # Неизвестная системная команда — пробуем запустить как msc/cpl
        import subprocess
        try:
            subprocess.Popen(["control", value], shell=True)
            return {"response": f"Выполняю: {value}"}
        except Exception as exc:
            return {"error": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
#  KEYWORD FALLBACK
# ═══════════════════════════════════════════════════════════════════════════

class KeywordFallback:
    _RULES = [
        (["блокнот", "notepad"],              "open_app",     {"app": "notepad"}),
        (["браузер", "browser", "интернет"],  "open_app",     {"app": "browser"}),
        (["калькулятор", "calculator"],       "open_app",     {"app": "calculator"}),
        (["ютуб", "youtube"],                 "open_url",     {"url": "https://youtube.com"}),
        (["гитхаб", "github"],                "open_url",     {"url": "https://github.com"}),
        (["вк", "vk", "вконтакте"],           "open_url",     {"url": "https://vk.com"}),
        (["телеграм", "telegram"],            "open_url",     {"url": "https://web.telegram.org"}),
        (["гугл", "google"],                  "open_url",     {"url": "https://google.com"}),
        (["проводник", "explorer"],           "open_system_app", {"app": "explorer"}),
        (["диспетчер задач"],                 "open_system_app", {"app": "taskmgr"}),
        (["панель управления"],               "open_system_app", {"app": "control"}),
        (["скриншот", "screenshot"],          "take_screenshot", {}),
        (["выключи компьютер"],               "shutdown_pc",  {}),
        (["перезагрузи"],                     "restart_pc",   {}),
        (["заблокируй"],                      "lock_pc",      {}),
        (["громче"],                          "set_volume",   {"action": "up"}),
        (["тише"],                            "set_volume",   {"action": "down"}),
        (["выключи звук", "mute"],            "set_volume",   {"action": "mute"}),
        (["который час", "время"],            "get_system_info", {"info_type": "time"}),
        (["характеристики", "сколько памяти"],"get_system_info", {"info_type": "all"}),
    ]

    def match(self, text: str) -> tuple | None:
        t = text.lower()
        for keywords, tool, args in self._RULES:
            if any(kw in t for kw in keywords):
                return tool, args
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  AI BRAIN
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class AIBrain:
    """
    ИИ-мозг GIDEON.
    Groq LLM → JSON → CommandExecutor → результат.
    """

    model:      str = ""
    max_tokens: int = 150    # JSON короткий, много не нужно

    _DEFAULT_MODEL = "llama-3.3-70b-versatile"

    _client:   Any             = field(default=None, init=False, repr=False)
    _executor: CommandExecutor = field(default_factory=CommandExecutor, init=False)
    _fallback: KeywordFallback = field(default_factory=KeywordFallback, init=False)
    _api_ok:   bool            = field(default=False, init=False)

    def __post_init__(self) -> None:
        if not self.model:
            self.model = os.environ.get("GROQ_MODEL", self._DEFAULT_MODEL)

        api_key = os.environ.get("GROQ_API_KEY", "").strip()
        if not api_key:
            logger.warning(
                "GROQ_API_KEY не найден. Работаю в режиме KeywordFallback. "
                "Получи ключ: https://console.groq.com"
            )
            return

        if not _GROQ_AVAILABLE:
            logger.warning("groq SDK не установлен. pip install groq")
            return

        try:
            self._client = Groq(api_key=api_key)
            self._api_ok = True
            logger.info("AIBrain (Groq) готов. Модель: %s", self.model)
        except Exception as exc:
            logger.error("Ошибка инициализации Groq: %s", exc)

    async def process(self, user_text: str, registry_execute) -> dict:
        if not self._api_ok or self._client is None:
            return await self._fallback_process(user_text, registry_execute)

        try:
            return await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._groq_call(user_text, registry_execute),
            )
        except Exception:
            logger.exception("Ошибка Groq, переключаюсь на fallback")
            return await self._fallback_process(user_text, registry_execute)

    def clear_history(self) -> None:
        pass

    @property
    def is_ai_active(self) -> bool:
        return self._api_ok and self._client is not None

    # ─── Groq вызов ───────────────────────────────────────────────────────

    def _groq_call(self, user_text: str, registry_execute) -> dict:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system",  "content": SYSTEM_PROMPT},
                {"role": "user",    "content": user_text},
            ],
            max_tokens=self.max_tokens,
            temperature=0.1,   # минимальная температура — нам нужен точный JSON
        )

        raw = response.choices[0].message.content or ""
        logger.info("Groq ответил: %s", raw.strip())

        cmd = self._parse_json(raw)
        if cmd is None:
            logger.warning("Не удалось распарсить JSON: %s", raw)
            return {"error": "Не понял команду"}

        # Выполняем в новом event loop (мы в thread executor)
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                self._executor.execute(cmd, registry_execute)
            )
        finally:
            loop.close()

    @staticmethod
    def _parse_json(text: str) -> dict | None:
        """Извлечь JSON из ответа модели."""
        text = text.strip()
        # Убрать markdown-обёртку если есть
        text = re.sub(r"```(?:json)?", "", text).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Попробовать найти JSON внутри текста
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
        return None

    async def _fallback_process(self, text: str, registry_execute) -> dict:
        match = self._fallback.match(text)
        if match:
            tool_name, args = match
            logger.info("[Fallback] %s(%s)", tool_name, args)
            return await registry_execute(tool_name, args)
        return {"error": "Команда не распознана. Попробуй иначе."}
