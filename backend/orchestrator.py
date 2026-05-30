"""
backend/orchestrator.py
Диспетчер: text → AIBrain (Gemini) → tool → state-update → result.
"""

import asyncio
import logging

from .ai_brain import AIBrain
from .voice_input import VoiceInput
from .voice_output import VoiceOutput, COMMON_PHRASES
from .tools import registry

logger = logging.getLogger("gideon.orchestrator")


class Orchestrator:
    """Главный диспетчер. Хранит state, выполняет команды через AIBrain."""

    def __init__(self) -> None:
        self.state      = "idle"
        self._broadcast = None
        self._brain     = AIBrain()
        self._voice     = VoiceInput()
        self._tts       = VoiceOutput()

        # Предзагрузить частые фразы в голосовой кэш (в фоне, не блокирует старт)
        if self._tts.enabled:
            import threading
            threading.Thread(
                target=self._tts.preload,
                args=(COMMON_PHRASES,),
                daemon=True,
                name="gideon-voice-preload",
            ).start()

        # Лог после инициализации AIBrain — теперь статус корректный
        mode = "Gemini API" if self._brain.is_ai_active else "KeywordFallback (нет ключа)"
        logger.info("Orchestrator запущен. Режим: %s", mode)

    def set_broadcast(self, fn) -> None:
        self._broadcast = fn

    async def list_commands(self) -> None:
        """Отправить frontend каталог доступных команд."""
        commands = self._brain.get_commands()
        logger.info("Запрошен список команд: %d шт.", len(commands))
        await self._send({"commands": commands})

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
        """
        Принять текст → составить план → ОЗВУЧИТЬ → выполнить → ответить.

        Порядок: Гидеон сначала говорит что собирается сделать
        («Открываю браузер»), и только после речи выполняет команду.
        """
        logger.info("Текстовая команда: %r", text)
        spoke_aloud = False
        try:
            # 1. THINK — LLM решает что делать (но ещё не выполняет)
            await self._set_state("think")
            cmd = await self._brain.plan(text)

            # 2. SPEAK — озвучить намерение ДО выполнения
            await self._set_state("speak")
            action = cmd.get("action", "")

            if action == "answer":
                phrase = cmd.get("value", "")
            elif action == "error":
                phrase = cmd.get("value", "Команда не распознана")
            else:
                phrase = self._brain.speech_for(cmd)

            # Отправить текст во frontend (подпись под сферой)
            await self._send({
                "state":    "speak",
                "status":   "error" if action == "error" else "success",
                "response": phrase,
            })

            # Озвучить намерение и ДОЖДАТЬСЯ конца речи
            if phrase and self._tts.enabled:
                await self._tts.speak_async(phrase)
                spoke_aloud = True

            # 3. ВЫПОЛНИТЬ команду (после того как договорил)
            if action not in ("answer", "error"):
                result = await self._brain.execute_plan(cmd, registry.execute)
                if "error" in result:
                    await self._send({
                        "state": "speak", "status": "error",
                        "response": result["error"],
                    })
                    if self._tts.enabled:
                        await self._tts.speak_async(result["error"])

        except Exception:
            logger.exception("Ошибка при обработке команды")
        finally:
            # ГАРАНТИРОВАННЫЙ возврат в покой — что бы ни случилось выше
            if not spoke_aloud:
                await asyncio.sleep(1.2)
            else:
                await asyncio.sleep(0.3)
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
