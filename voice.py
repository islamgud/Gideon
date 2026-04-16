"""
voice.py — голосовой модуль GIDEON

STT: SpeechRecognition + микрофон (Google Speech API, бесплатно)
TTS: edge-tts (Microsoft Neural, ru-RU-DmitryNeural)

Установка:
    pip install SpeechRecognition pyaudio edge-tts

PyAudio на Windows: pip install pipwin && pipwin install pyaudio
PyAudio на Mac:     brew install portaudio && pip install pyaudio
PyAudio на Linux:   sudo apt install portaudio19-dev && pip install pyaudio
"""

import asyncio
import logging
import tempfile
import os
import subprocess

logger = logging.getLogger("gideon.voice")

# ── Голос GIDEON ──────────────────────────────────────────────
# ru-RU-DmitryNeural — мужской, чёткий, нейтральный
# Другие варианты: ru-RU-SvetlanaNeural (женский)
TTS_VOICE = "ru-RU-DmitryNeural"
TTS_RATE  = "-5%"   # чуть медленнее стандарта — солиднее звучит
TTS_PITCH = "-8Hz"  # чуть ниже — холоднее, технологичнее


# ══════════════════════════════════════════════════════════════
#  TTS — синтез речи
# ══════════════════════════════════════════════════════════════
async def speak(text: str) -> None:
    """
    Синтезировать текст голосом GIDEON и воспроизвести.
    Блокирует до конца воспроизведения.
    """
    if not text or not text.strip():
        return

    logger.info("TTS: %r", text[:60])

    try:
        import edge_tts

        # Генерируем mp3 во временный файл
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            tmp_path = f.name

        communicate = edge_tts.Communicate(
            text=text,
            voice=TTS_VOICE,
            rate=TTS_RATE,
            pitch=TTS_PITCH,
        )
        await communicate.save(tmp_path)

        # Воспроизвести через системный плеер
        await _play_audio(tmp_path)

    except ImportError:
        logger.error("edge-tts не установлен: pip install edge-tts")
    except Exception as e:
        logger.error("TTS ошибка: %s", e)
    finally:
        # Удалить временный файл
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


async def _play_audio(path: str) -> None:
    """Воспроизвести аудиофайл через системный плеер."""
    loop = asyncio.get_event_loop()

    def _play():
        # Windows
        if os.name == "nt":
            os.startfile(path)
            import time; time.sleep(3)  # грубо, но работает без зависимостей
            return

        # Mac
        if _cmd_exists("afplay"):
            subprocess.run(["afplay", path], check=False)
            return

        # Linux — пробуем по порядку
        for player in ("mpg123", "mpg321", "ffplay", "aplay"):
            if _cmd_exists(player):
                if player == "ffplay":
                    subprocess.run(
                        ["ffplay", "-nodisp", "-autoexit", path],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    subprocess.run([player, path], check=False,
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
                return

        logger.warning(
            "Нет аудиоплеера. Установите: sudo apt install mpg123"
        )

    await loop.run_in_executor(None, _play)


def _cmd_exists(cmd: str) -> bool:
    """Проверить что команда доступна в PATH."""
    import shutil
    return shutil.which(cmd) is not None


# ══════════════════════════════════════════════════════════════
#  STT — распознавание речи с микрофона
# ══════════════════════════════════════════════════════════════
class VoiceListener:
    """
    Слушает микрофон и возвращает распознанный текст.

    Использование:
        listener = VoiceListener()
        text = await listener.listen_once()
    """

    def __init__(self, language: str = "ru-RU", timeout: float = 5.0):
        self.language = language
        self.timeout  = timeout
        self._recognizer = None
        self._mic        = None
        self._ready      = False

    def setup(self) -> bool:
        """Инициализировать распознаватель. Вернуть True если успешно."""
        try:
            import speech_recognition as sr
            self._recognizer = sr.Recognizer()
            self._mic        = sr.Microphone()

            # Калибровка уровня шума (1 секунда)
            with self._mic as source:
                logger.info("Калибровка микрофона...")
                self._recognizer.adjust_for_ambient_noise(source, duration=1.0)

            self._ready = True
            logger.info("Микрофон готов (язык: %s)", self.language)
            return True

        except ImportError:
            logger.error(
                "SpeechRecognition не установлен: pip install SpeechRecognition pyaudio"
            )
            return False
        except Exception as e:
            logger.error("Ошибка микрофона: %s", e)
            return False

    async def listen_once(self) -> str | None:
        """
        Ждать слово/фразу, распознать, вернуть текст.
        Возвращает None при ошибке или тишине.
        """
        if not self._ready:
            logger.warning("Микрофон не инициализирован")
            return None

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._blocking_listen)

    def _blocking_listen(self) -> str | None:
        """Синхронная часть — запускается в threadpool."""
        import speech_recognition as sr

        try:
            with self._mic as source:
                logger.info("Слушаю...")
                audio = self._recognizer.listen(
                    source,
                    timeout=self.timeout,
                    phrase_time_limit=10,
                )

            text = self._recognizer.recognize_google(
                audio,
                language=self.language,
            )
            logger.info("Распознано: %r", text)
            return text.strip()

        except sr.WaitTimeoutError:
            logger.debug("Тишина — таймаут")
            return None
        except sr.UnknownValueError:
            logger.debug("Речь не распознана")
            return None
        except sr.RequestError as e:
            logger.error("Ошибка API распознавания: %s", e)
            return None


# ══════════════════════════════════════════════════════════════
#  WAKE WORD LOOP — непрерывное ожидание "гидеон"
# ══════════════════════════════════════════════════════════════
WAKE_WORDS = ["гидеон", "gideon", "эй", "привет"]

async def wake_word_loop(on_wake, on_command) -> None:
    """
    Фоновая задача: слушает микрофон непрерывно.

    on_wake(text)    — вызывается при обнаружении wake-слова
    on_command(text) — вызывается при любой другой фразе

    Оба callback'а — async-функции.
    """
    listener = VoiceListener()
    if not listener.setup():
        logger.error("Голосовой ввод недоступен")
        return

    logger.info("Wake-word loop запущен. Скажите 'Гидеон'...")

    while True:
        text = await listener.listen_once()
        if not text:
            continue

        lower = text.lower()
        if any(w in lower for w in WAKE_WORDS):
            await on_wake(text)
        else:
            await on_command(text)

        # Небольшая пауза чтобы не перегружать API
        await asyncio.sleep(0.2)
