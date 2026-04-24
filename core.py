import json
import asyncio
import subprocess
import platform
import logging
from openai import AsyncOpenAI

logger = logging.getLogger("gideon.core")

_openai = AsyncOpenAI()

_OS = platform.system()

ALLOWED_APPS: dict[str, list[str]] = {
    "browser": {
        "Windows": ["cmd", "/c", "start", "chrome"],
        "Darwin":  ["open", "-a", "Google Chrome"],
        "Linux":   ["xdg-open", "https://google.com"],
    }.get(_OS, []),

    "notepad": {
        "Windows": ["notepad"],
        "Darwin":  ["open", "-a", "TextEdit"],
        "Linux":   ["gedit"],
    }.get(_OS, []),

    "terminal": {
        "Windows": ["cmd", "/c", "start", "cmd"],
        "Darwin":  ["open", "-a", "Terminal"],
        "Linux":   ["x-terminal-emulator"],
    }.get(_OS, []),
}

VALID_INTENTS = {
    "wake", "sleep", "idle", "listen", "think",
    "open_app", "memory_set", "memory_get", "status", "unknown"
}

STATE_INTENTS = {"wake", "sleep", "idle", "listen", "think"}

_INTENT_RESPONSES = {
    "wake":   "Я слушаю",
    "sleep":  "Ухожу в сон",
    "idle":   "На паузе",
    "listen": "Слушаю внимательно",
    "think":  "Думаю...",
}

_SYSTEM_PROMPT = """
Ты — парсер команд для голосового ассистента GIDEON.
Преобразуй команду пользователя в JSON-объект.

Доступные intent-ы:
  wake         — пользователь будит ассистента
  sleep        — перевести в спящий режим
  idle         — поставить на паузу
  listen       — режим прослушивания
  think        — режим обдумывания
  open_app     — открыть приложение (params: {"app": "browser"|"notepad"|"terminal"})
  memory_set   — запомнить значение (params: {"key": "...", "value": "..."})
  memory_get   — показать память
  status       — запросить текущее состояние
  unknown      — если команда непонятна

Отвечай ТОЛЬКО валидным JSON, без markdown-блоков, без пояснений.
Пример: {"intent": "open_app", "params": {"app": "browser"}}
""".strip()


class GideonCore:

    def __init__(self):
        self.state     = "idle"
        self.memory    = {}
        self._broadcast = None
        self._tts      = True

    def get_state(self) -> str:
        return self.state

    def set_broadcast(self, fn):
        self._broadcast = fn

    def set_tts(self, enabled: bool):
        self._tts = enabled

    # ══════════════════════════════════════════════════════════
    #  State
    # ══════════════════════════════════════════════════════════
    def _update_state(self, intent: str):
        if intent in STATE_INTENTS:
            self.state = intent

    # ══════════════════════════════════════════════════════════
    #  LLM
    # ══════════════════════════════════════════════════════════
    async def understand(self, text: str) -> dict:
        response = await asyncio.wait_for(
            _openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": text},
                ],
                temperature=0,
                max_tokens=150,
                response_format={"type": "json_object"},
            ),
            timeout=10,
        )

        return json.loads(response.choices[0].message.content)

    # ══════════════════════════════════════════════════════════
    #  Обработка команды
    # ══════════════════════════════════════════════════════════
    async def process_command(self, text: str) -> dict:
        try:
            parsed = await self.understand(text)
        except asyncio.TimeoutError:
            logger.error("understand() timeout")
            return {"state": self.state, "response": "LLM не ответил вовремя"}
        except Exception as e:
            logger.error("understand() failed: %s", e)
            return {"state": self.state, "response": "Ошибка анализа команды"}

        if "intent" not in parsed:
            logger.warning("LLM не вернул intent: %r", parsed)
            return {"state": self.state, "response": "Не понял команду"}

        intent = parsed.get("intent", "unknown")

        if intent not in VALID_INTENTS:
            logger.warning("Неизвестный intent от LLM: %r", intent)
            intent = "unknown"

        params = parsed.get("params", {})
        logger.info("intent=%r params=%r", intent, params)

        # до любых side effects
        if intent == "status":
            return {"state": self.state}

        # unknown не трогает state pipeline
        if intent != "unknown":
            self._update_state(intent)

        match intent:
            case "unknown":
                return {"state": self.state, "response": "Не понял команду"}
            case "open_app":
                return await self._handle_open_app(params)
            case "memory_set":
                return self._handle_memory_set(params)
            case "memory_get":
                return {"state": self.state, "memory": self.memory}

        # wake / sleep / idle / listen / think
        result = {"state": self.state}
        response = _INTENT_RESPONSES.get(intent)
        if response:
            result["response"] = response
        return result

    # ══════════════════════════════════════════════════════════
    #  Хэндлеры
    # ══════════════════════════════════════════════════════════
    async def _handle_open_app(self, params: dict) -> dict:
        app = params.get("app", "").lower()

        if app not in ALLOWED_APPS or not ALLOWED_APPS[app]:
            return {"state": self.state, "error": f"Приложение '{app}' не разрешено"}

        try:
            subprocess.Popen(ALLOWED_APPS[app], shell=False)
        except FileNotFoundError:
            return {"state": self.state, "error": f"Не найдено: {ALLOWED_APPS[app][0]}"}
        except Exception as e:
            logger.exception("Ошибка запуска %s", app)
            return {"state": self.state, "error": str(e)}

        return {"state": self.state, "response": f"Открываю {app}"}

    def _handle_memory_set(self, params: dict) -> dict:
        key   = params.get("key",   "").strip()
        value = params.get("value", "").strip()

        if not key:
            return {"state": self.state, "error": "Не указан ключ"}

        self.memory[key] = value
        return {"state": self.state, "response": f"Запомнил: {key} = {value}"}
