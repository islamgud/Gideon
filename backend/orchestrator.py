"""
backend/orchestrator.py
Диспетчер: text → AIBrain (Gemini) → tool → state-update → result.
"""

import asyncio
import logging

from .ai_brain import AIBrain
from .voice_input import VoiceInput
from .tools import registry

logger = logging.getLogger("gideon.orchestrator")


class Orchestrator:
    """Главный диспетчер. Хранит state, выполняет команды через AIBrain."""

    def __init__(self) -> None:
        self.state      = "idle"
        self._broadcast = None
        self._brain     = AIBrain()
        self._voice     = VoiceInput()

        # Лог после инициализации AIBrain — теперь статус корректный
        mode = "Groq API" if self._brain.is_ai_active else "KeywordFallback (нет ключа)"
        logger.info("Orchestrator запущен. Режим: %s", mode)

    def set_broadcast(self, fn) -> None:
        self._broadcast = fn

    # ─── State ───────────────────────────────────────────────────────────

    async def _set_state(self, new_state: str) -> None:
        if new_state == self.state:
            return
        self.state = new_state
        logger.info("State → %s", new_state)
        if self._broadcast:
            await self._broadcast({"state": new_state})

    async def _send(self, payload: dict) -> None:
        if self._broadcast:
            await self._broadcast(payload)

    # ─── Точки входа ─────────────────────────────────────────────────────

    async def process(self, text: str) -> None:
        """Обратная совместимость — делегирует в process_text_command."""
        await self.process_text_command(text)

    async def process_text_command(self, text: str) -> None:
        """Принять текст → AIBrain → tool или диалог → ответ пользователю."""
        logger.info("Текстовая команда: %r", text)

        await self._set_state("think")

        result = await self._brain.process(text, registry.execute)

        await self._set_state("speak")
        payload = {"state": "speak"}
        if "response" in result:
            payload["response"] = result["response"]
            payload["status"]   = "success"
        else:
            payload["response"] = result.get("error", "Ошибка")
            payload["status"]   = "error"
        await self._send(payload)

        await asyncio.sleep(2.0)
        await self._set_state("idle")

    async def process_voice_command(self) -> None:
        """Голосовой ввод → AIBrain → ответ."""
        logger.info("Голосовая команда")

        await self._set_state("listen")
        await self._send({"state": "listen", "status": "listening"})

        voice_result = await self._voice.recognize_async()

        if not voice_result.success:
            await self._set_state("speak")
            await self._send({
                "state":    "speak",
                "status":   "error",
                "response": voice_result.error or "Не удалось распознать речь",
            })
            await asyncio.sleep(2.0)
            await self._set_state("idle")
            return

        recognized = voice_result.text
        logger.info("Распознано: %r", recognized)
        await self._send({"state": "think", "recognized": recognized})
        await self.process_text_command(recognized)
