"""
backend/orchestrator.py
Диспетчер: text → intent → tool → state-update → result.

Изменения относительно оригинала:
  * Добавлен CommandProcessor для расширяемой маршрутизации команд.
  * Добавлен VoiceInput для голосового ввода.
  * Новые публичные методы: process_text_command(), process_voice_command().
  * Оригинальный _parse_command() сохранён для обратной совместимости,
    но process() теперь делегирует в CommandProcessor.
"""

import asyncio
import logging
from typing import Optional

from .command_processor import CommandProcessor
from .voice_input import VoiceInput
from .tools import registry  # noqa: F401

logger = logging.getLogger("gideon.orchestrator")


class Orchestrator:
    """Главный диспетчер. Хранит state, выполняет команды."""

    def __init__(self) -> None:
        self.state = "idle"
        self._broadcast = None

        # Инициализация подсистем
        self._command_processor = CommandProcessor()
        self._voice_input       = VoiceInput()

    def set_broadcast(self, fn) -> None:
        """Подключить функцию рассылки сообщений всем клиентам."""
        self._broadcast = fn

    # ─── State ───────────────────────────────────────────────────────────

    async def _set_state(self, new_state: str) -> None:
        """Сменить state и отправить frontend."""
        if new_state == self.state:
            return
        self.state = new_state
        logger.info("State: %s", new_state)
        if self._broadcast:
            await self._broadcast({"state": new_state})

    # ─── Основные публичные методы ───────────────────────────────────────

    async def process(self, text: str) -> None:
        """
        Обработать текстовую команду (точка входа из ws_server).
        Делегирует в process_text_command().
        """
        await self.process_text_command(text)

    async def process_text_command(self, text: str) -> None:
        """
        Принять текст, распознать команду, выполнить, ответить.

        Этапы:
          1. THINK — анализируем
          2. CommandProcessor.execute() — ищем и запускаем tool
          3. SPEAK  — отвечаем результатом
          4. IDLE   — возвращаемся в покой
        """
        logger.info("Текстовая команда: %r", text)

        await self._set_state("think")
        await asyncio.sleep(0.3)

        result = await self._command_processor.execute(text)

        await self._set_state("speak")
        if self._broadcast:
            # Нормализуем ключ: поддерживаем как "response"/"error", так и "message"
            payload = {"state": "speak"}
            if "response" in result:
                payload["response"] = result["response"]
                payload["status"]   = "success"
            elif "error" in result:
                payload["response"] = result["error"]
                payload["status"]   = "error"
            await self._broadcast(payload)

        await asyncio.sleep(2.0)
        await self._set_state("idle")

    async def process_voice_command(self) -> None:
        """
        Запустить голосовой ввод и обработать распознанную команду.

        Этапы:
          1. LISTEN  — сигнализируем frontend, слушаем микрофон
          2. THINK   — распознаём → CommandProcessor
          3. SPEAK   — отвечаем
          4. IDLE
        """
        logger.info("Запрос голосовой команды")

        # 1. LISTEN
        await self._set_state("listen")
        if self._broadcast:
            await self._broadcast({"state": "listen", "status": "listening"})

        # Захват аудио в executor (блокирующий вызов)
        voice_result = await self._voice_input.recognize_async()

        if not voice_result.success:
            # Не удалось захватить/распознать
            await self._set_state("speak")
            if self._broadcast:
                await self._broadcast({
                    "state":    "speak",
                    "status":   "error",
                    "response": voice_result.error or "Не удалось распознать речь",
                })
            await asyncio.sleep(2.0)
            await self._set_state("idle")
            return

        recognized_text = voice_result.text
        logger.info("Распознана команда: %r", recognized_text)

        # Уведомляем frontend о распознанном тексте
        if self._broadcast:
            await self._broadcast({
                "state":      "think",
                "recognized": recognized_text,
            })

        # 2. THINK → обработка
        await self.process_text_command(recognized_text)
