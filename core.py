"""
core.py — управляющее ядро GIDEON

Состояния:
    sleep   — спит, не реагирует
    idle    — готов, ждёт
    listen  — слушает пользователя
    think   — обрабатывает запрос
    speak   — говорит (TTS)

Поток: idle → listen → think → speak → idle
"""

import asyncio
import logging
from memory import Memory

logger = logging.getLogger("gideon.core")


# ── Таблица интентов (ключевые слова, русский) ────────────────
INTENT_MAP = {
    "wake":          ["гидеон", "gideon", "эй гидеон", "hey gideon"],
    "sleep":         ["спать", "выключись", "пока", "sleep", "bye", "отдыхай"],
    "think":         ["подумай", "думай", "анализируй", "посчитай", "think", "analyze"],
    "listen":        ["слушай", "слушаю", "начинай", "listen"],
    "stop":          ["стоп", "хватит", "отмена", "stop", "cancel", "тихо"],
    "status":        ["статус", "как дела", "что делаешь", "status"],
    "memory_save":   ["запомни", "сохрани", "remember"],
    "memory_load":   ["вспомни", "что знаешь", "recall", "память"],
    "memory_clear":  ["забудь", "очисти память", "forget"],
}

# Какое состояние устанавливает каждый интент (None = не меняет)
TRANSITIONS = {
    "wake":         "idle",
    "sleep":        "sleep",
    "think":        "think",
    "listen":       "listen",
    "stop":         "idle",
    "status":       None,
    "memory_save":  None,
    "memory_load":  None,
    "memory_clear": None,
}

# Голосовые ответы GIDEON
VOICE_RESPONSES = {
    "wake":         "Здесь. Слушаю.",
    "sleep":        "Ухожу в режим ожидания.",
    "think":        "Анализирую.",
    "listen":       "Говорите.",
    "stop":         "Принято.",
    "memory_clear": "Память очищена.",
    "unknown":      "Не понял команду.",
}


class GideonCore:
    def __init__(self, broadcast_fn=None, tts_enabled: bool = True):
        self.state: str   = "idle"
        self.memory       = Memory()
        self._broadcast   = broadcast_fn
        self._tts_enabled = tts_enabled
        self._lock        = asyncio.Lock()
        logger.info("GideonCore инициализирован. Состояние: %s", self.state)

    def set_broadcast(self, fn) -> None:
        self._broadcast = fn

    def set_tts(self, enabled: bool) -> None:
        self._tts_enabled = enabled

    # ══════════════════════════════════════════════════════════
    #  ГЛАВНЫЙ МЕТОД
    # ══════════════════════════════════════════════════════════
    async def process_command(self, text: str) -> dict:
        """Принять команду → интент → состояние → TTS → фронтенд."""
        async with self._lock:
            text = text.strip()
            if not text:
                return {"ok": False, "reason": "empty"}

            logger.info("Команда: %r  |  Состояние: %s", text, self.state)
            intent = self._detect_intent(text)
            logger.info("Интент: %s", intent)
            return await self._handle_intent(intent, text)

    # ══════════════════════════════════════════════════════════
    #  ОПРЕДЕЛЕНИЕ ИНТЕНТА
    # ══════════════════════════════════════════════════════════
    def _detect_intent(self, text: str) -> str:
        """
        Keyword-матчинг с приоритетом длины.
        Более длинное совпавшее слово = более специфичный интент.
        """
        lower = text.lower()
        best_intent, best_len = "unknown", 0
        for intent, keywords in INTENT_MAP.items():
            for kw in keywords:
                if kw in lower and len(kw) > best_len:
                    best_intent = intent
                    best_len    = len(kw)
        return best_intent

    # ══════════════════════════════════════════════════════════
    #  ОБРАБОТКА ИНТЕНТОВ
    # ══════════════════════════════════════════════════════════
    async def _handle_intent(self, intent: str, text: str) -> dict:
        # Спим — реагируем только на wake
        if self.state == "sleep" and intent != "wake":
            return {"ok": False, "reason": "sleeping", "intent": intent}

        # ── Специальные команды ───────────────────────────────
        if intent == "memory_save":
            return await self._cmd_memory_save(text)

        if intent == "memory_load":
            entries = self.memory.all()
            await self._say(self._format_memory(entries))
            return {"ok": True, "intent": intent, "memory": entries}

        if intent == "memory_clear":
            self.memory.clear()
            await self._say(VOICE_RESPONSES["memory_clear"])
            return {"ok": True, "intent": intent}

        if intent == "status":
            await self._say(f"Состояние: {self.state}. Все системы в норме.")
            return {"ok": True, "intent": intent, "state": self.state}

        # ── Переход состояния ─────────────────────────────────
        new_state = TRANSITIONS.get(intent)

        if intent == "unknown":
            if self.state in ("idle", "listen"):
                # Неизвестная фраза в активном состоянии → think
                new_state = "think"
                intent    = "think"
            else:
                await self._say(VOICE_RESPONSES["unknown"])
                return {"ok": False, "reason": "unknown intent", "state": self.state}

        if new_state and new_state != self.state:
            await self._set_state(new_state)

        # Голосовой ответ на переход
        reply = VOICE_RESPONSES.get(intent)
        if reply:
            await self._say(reply)

        return {"ok": True, "intent": intent, "state": self.state}

    # ── "запомни X = Y" ───────────────────────────────────────
    async def _cmd_memory_save(self, text: str) -> dict:
        for sep in ("=", " это ", " is "):
            if sep in text.lower():
                parts = text.lower().split(sep, 1)
                key   = parts[0]
                for kw in ("запомни", "сохрани", "remember"):
                    key = key.replace(kw, "").strip()
                value = parts[1].strip()
                self.memory.save(key, value)
                await self._say(f"Запомнил: {key} — {value}.")
                return {"ok": True, "intent": "memory_save", "key": key, "value": value}

        await self._say("Не понял что запомнить. Скажите: запомни ключ равно значение.")
        return {"ok": False, "reason": "parse error", "intent": "memory_save"}

    def _format_memory(self, entries: dict) -> str:
        parts = [f"{k}: {v}" for k, v in entries.items() if not k.startswith("last_")]
        return ("Я помню: " + ". ".join(parts) + ".") if parts else "Память пуста."

    # ══════════════════════════════════════════════════════════
    #  УПРАВЛЕНИЕ СОСТОЯНИЕМ
    # ══════════════════════════════════════════════════════════
    async def _set_state(self, new_state: str) -> None:
        old = self.state
        self.state = new_state
        logger.info("Состояние: %s → %s", old, new_state)
        self.memory.save("last_state", new_state)
        await self._notify_frontend(new_state)

    async def _notify_frontend(self, state: str) -> None:
        if self._broadcast is None:
            return
        import json
        payload = json.dumps({"state": state})
        await self._broadcast(payload)
        logger.info("→ Frontend: %s", state)

    # ══════════════════════════════════════════════════════════
    #  TTS — произнести текст голосом GIDEON
    # ══════════════════════════════════════════════════════════
    async def _say(self, text: str | None) -> None:
        """
        Произнести текст:
        1. Переключить фронтенд в speak
        2. Дождаться конца воспроизведения
        3. Вернуть фронтенд в idle
        """
        if not text or not self._tts_enabled:
            return

        await self._notify_frontend("speak")
        try:
            from voice import speak
            await speak(text)
        except Exception as e:
            logger.error("TTS ошибка: %s", e)
        finally:
            # Возврат в текущее состояние (не всегда idle)
            await self._notify_frontend(self.state)

    # ══════════════════════════════════════════════════════════
    #  УТИЛИТЫ
    # ══════════════════════════════════════════════════════════
    def get_state(self) -> str:
        return self.state

    def status(self) -> dict:
        return {
            "state":       self.state,
            "tts":         self._tts_enabled,
            "memory_keys": list(self.memory.all().keys()),
        }
