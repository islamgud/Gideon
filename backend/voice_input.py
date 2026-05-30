"""
backend/voice_input.py
Голосовой ввод команд.

Использует speech_recognition (Google Speech Recognition) для
распознавания русской речи с микрофона.

Класс VoiceInput:
  listen()     — захватить аудио с микрофона
  recognize()  — распознать речь → строка команды

Обрабатываемые ошибки:
  * Микрофон не найден / OSError
  * Ничего не распознано (UnknownValueError)
  * Ошибка сети / RequestError
  * Превышение ожидания (WaitTimeoutError)
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("gideon.voice_input")

# speech_recognition — опциональная зависимость (импортируем лениво)
try:
    import speech_recognition as sr
    _SR_AVAILABLE = True
except ImportError:
    _SR_AVAILABLE = False
    logger.warning(
        "speech_recognition не установлен. "
        "Голосовой ввод недоступен. "
        "Установите: pip install SpeechRecognition pyaudio"
    )


@dataclass
class VoiceResult:
    """Результат распознавания речи."""
    success: bool
    text:    str = ""       # распознанный текст (если success=True)
    error:   str = ""       # описание ошибки (если success=False)


@dataclass
class VoiceInput:
    """
    Голосовой ввод на базе Google Speech Recognition.

    Параметры:
      language        — язык распознавания (по умолчанию ru-RU)
      energy_threshold — порог чувствительности микрофона
      pause_threshold  — пауза (сек), после которой запись считается завершённой
      timeout          — максимальное ожидание начала речи (сек)
      phrase_limit     — максимальная длина фразы (сек)
    """

    language:         str   = "ru-RU"
    energy_threshold: int   = 300
    pause_threshold:  float = 0.8
    timeout:          float = 5.0
    phrase_limit:     float = 10.0

    # Внутренний Recognizer создаётся при первом использовании
    _recognizer: Optional[object] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if _SR_AVAILABLE:
            self._recognizer = sr.Recognizer()
            self._recognizer.energy_threshold = self.energy_threshold
            self._recognizer.pause_threshold  = self.pause_threshold
            self._recognizer.dynamic_energy_threshold = True

    # ─── Публичные методы ────────────────────────────────────────────

    def listen(self) -> VoiceResult:
        """
        Синхронно захватить аудио с микрофона.
        Возвращает VoiceResult с аудиоданными в поле text=None
        (промежуточный результат; используйте recognize() для полного цикла).

        Обычно вызывайте recognize() — он оборачивает оба шага.
        """
        return self._capture_audio()

    def recognize(self) -> VoiceResult:
        """
        Захватить аудио с микрофона и распознать речь.
        Возвращает VoiceResult.
        """
        if not _SR_AVAILABLE:
            return VoiceResult(
                success=False,
                error="Модуль speech_recognition не установлен",
            )

        # 1. Захват
        capture = self._capture_audio()
        if not capture.success:
            return capture

        # 2. Распознавание (audio хранится в capture.text как объект AudioData)
        return self._do_recognize(capture._audio)  # type: ignore[attr-defined]

    async def recognize_async(self) -> VoiceResult:
        """
        Асинхронная обёртка — запускает recognize() в thread-pool,
        чтобы не блокировать asyncio event loop.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.recognize)

    # ─── Wake-word («Гидеон») ──────────────────────────────────────────

    def listen_for_wakeword(self, wake_words: list, phrase_limit: float = 3.0) -> bool:
        """
        Слушать короткий отрезок и проверить, прозвучало ли слово активации.
        Возвращает True если услышал «гидеон» (или вариант), иначе False.

        Используется в фоновом цикле — поэтому ошибки/тишина просто дают False,
        без шумных логов на каждой итерации.
        """
        if not _SR_AVAILABLE or self._recognizer is None:
            return False
        try:
            with sr.Microphone() as source:
                self._recognizer.adjust_for_ambient_noise(source, duration=0.2)
                audio = self._recognizer.listen(
                    source, timeout=4.0, phrase_time_limit=phrase_limit
                )
        except (sr.WaitTimeoutError, OSError):
            return False
        except Exception:
            return False

        try:
            text = self._recognizer.recognize_google(audio, language=self.language)
        except (sr.UnknownValueError, sr.RequestError):
            return False
        except Exception:
            return False

        heard = text.lower().strip()
        for w in wake_words:
            if w in heard:
                logger.info("Wake-word услышан: %r (в %r)", w, heard)
                return True
        return False

    async def listen_for_wakeword_async(self, wake_words: list) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self.listen_for_wakeword, wake_words
        )

    # ─── Внутренние методы ───────────────────────────────────────────

    def _capture_audio(self) -> "VoiceResult":
        """Захват аудио с микрофона."""
        try:
            with sr.Microphone() as source:
                logger.info("Калибровка микрофона...")
                self._recognizer.adjust_for_ambient_noise(source, duration=0.4)
                logger.info("Слушаю... (timeout=%.1fs)", self.timeout)
                audio = self._recognizer.listen(
                    source,
                    timeout=self.timeout,
                    phrase_time_limit=self.phrase_limit,
                )
                # Сохраняем audio в «скрытом» поле для передачи в _do_recognize
                result = VoiceResult(success=True, text="__audio__")
                result._audio = audio  # type: ignore[attr-defined]
                return result

        except OSError as exc:
            msg = "Микрофон не найден или занят"
            logger.error("%s: %s", msg, exc)
            return VoiceResult(success=False, error=msg)

        except sr.WaitTimeoutError:
            msg = "Время ожидания истекло — речь не обнаружена"
            logger.warning(msg)
            return VoiceResult(success=False, error=msg)

        except Exception as exc:
            logger.exception("Неожиданная ошибка при захвате аудио")
            return VoiceResult(success=False, error=str(exc))

    def _do_recognize(self, audio) -> VoiceResult:
        """Распознать аудио через Google Speech Recognition."""
        try:
            text = self._recognizer.recognize_google(audio, language=self.language)
            logger.info("Распознано: %r", text)
            return VoiceResult(success=True, text=text)

        except sr.UnknownValueError:
            msg = "Речь не распознана"
            logger.warning(msg)
            return VoiceResult(success=False, error=msg)

        except sr.RequestError as exc:
            msg = f"Ошибка сети при распознавании: {exc}"
            logger.error(msg)
            return VoiceResult(success=False, error=msg)

        except Exception as exc:
            logger.exception("Неожиданная ошибка при распознавании")
            return VoiceResult(success=False, error=str(exc))
