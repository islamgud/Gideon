"""
backend/voice_output.py
═══════════════════════════════════════════════════════════════════════════════
Голос GIDEON — синтез речи (TTS) с фирменным «подводным/пространственным»
эффектом. Делает голос ядра атмосферным, глубоким, ни на кого не похожим.

База:    edge-tts (нейросетевые голоса Microsoft, бесплатно, нужен интернет)
Эффекты: pydub + numpy — понижение тона, low-pass фильтр (под водой),
         reverb (глубина/пространство), flanger (колыхание течения)

Класс VoiceOutput:
  speak(text)        — синхронно озвучить текст с эффектами
  speak_async(text)  — асинхронная обёртка для asyncio

Зависимости:
  pip install edge-tts pydub numpy
  + ffmpeg в системе (pydub требует для обработки/воспроизведения mp3)

Если edge-tts недоступен (нет интернета) — голос просто пропускается,
ассистент продолжает работать молча (текстовые ответы остаются).
"""

import asyncio
import io
import logging
import os
import tempfile

logger = logging.getLogger("gideon.voice_output")

# ── Зависимости (опциональны — без них голос просто отключается) ─────────────
try:
    import edge_tts
    _EDGE_AVAILABLE = True
except ImportError:
    _EDGE_AVAILABLE = False
    logger.warning("edge-tts не установлен. pip install edge-tts")

try:
    from pydub import AudioSegment
    from pydub.playback import play
    import numpy as np
    _AUDIO_AVAILABLE = True
except ImportError:
    _AUDIO_AVAILABLE = False
    logger.warning("pydub/numpy не установлены. pip install pydub numpy (+ ffmpeg)")


# ═══════════════════════════════════════════════════════════════════════════
#  ПОДВОДНЫЕ / ПРОСТРАНСТВЕННЫЕ ЭФФЕКТЫ
# ═══════════════════════════════════════════════════════════════════════════

# Частые фразы-намерения — предзагружаются в кэш при старте для мгновенной речи.
COMMON_PHRASES = [
    "Открываю браузер",
    "Открываю приложение",
    "Открываю сайт",
    "Открываю папку загрузки",
    "Открываю документы",
    "Открываю рабочий стол",
    "Выключаю компьютер",
    "Перезагружаю компьютер",
    "Блокирую экран",
    "Делаю скриншот",
    "Прибавляю громкость",
    "Убавляю громкость",
    "Выключаю звук",
    "Включаю звук",
    "Меняю громкость",
    "Меняю язык",
    "Выполняю",
    "Секунду",
    "Привет! Чем могу помочь?",
    "Команда не распознана. Попробуй иначе.",
]


class UnderwaterFX:
    """
    Набор аудио-эффектов, превращающих обычный голос в «голос из глубины».

    Параметры (можно крутить под себя):
      pitch_semitones  — сдвиг тона в полутонах (отрицательный = ниже). По умолч. -2.5
      lowpass_hz       — частота среза ВЧ (чем ниже, тем глуше/«подводнее"). 2200 Гц
      reverb_ms        — длительность хвоста эха (глубина пространства). 220 мс
      reverb_decay     — затухание эха 0..1 (больше = дольше звенит). 0.35
      flanger_depth_ms — глубина колыхания (эффект течения). 3.0 мс
      flanger_rate_hz  — скорость колыхания. 0.25 Гц
      wet              — баланс эффект/сухой сигнал 0..1. 0.85
    """

    def __init__(
        self,
        pitch_semitones: float = -2.5,
        lowpass_hz: int = 2200,
        reverb_ms: int = 150,
        reverb_decay: float = 0.22,
        flanger_depth_ms: float = 3.0,
        flanger_rate_hz: float = 0.25,
        wet: float = 0.85,
    ) -> None:
        self.pitch_semitones = pitch_semitones
        self.lowpass_hz = lowpass_hz
        self.reverb_ms = reverb_ms
        self.reverb_decay = reverb_decay
        self.flanger_depth_ms = flanger_depth_ms
        self.flanger_rate_hz = flanger_rate_hz
        self.wet = wet

    def apply(self, seg: "AudioSegment") -> "AudioSegment":
        """Применить всю цепочку эффектов к аудиосегменту."""
        seg = seg.set_channels(1)            # моно для обработки
        seg = self._pitch_shift(seg)         # 1. понизить тон
        seg = self._lowpass(seg)             # 2. приглушить ВЧ (под водой)
        seg = self._flanger(seg)             # 3. колыхание (течение)
        seg = self._reverb(seg)              # 4. пространство/глубина
        seg = seg.set_channels(2)            # стерео на выходе
        return seg

    # ── 1. Сдвиг тона (понижение) ─────────────────────────────────────────
    def _pitch_shift(self, seg: "AudioSegment") -> "AudioSegment":
        ratio = 2.0 ** (self.pitch_semitones / 12.0)
        new_rate = int(seg.frame_rate * ratio)
        shifted = seg._spawn(seg.raw_data, overrides={"frame_rate": new_rate})
        return shifted.set_frame_rate(seg.frame_rate)

    # ── 2. Low-pass фильтр (эффект «под водой») ───────────────────────────
    def _lowpass(self, seg: "AudioSegment") -> "AudioSegment":
        # Двойной проход для более крутого среза
        seg = seg.low_pass_filter(self.lowpass_hz)
        seg = seg.low_pass_filter(self.lowpass_hz)
        return seg

    # ── 3. Flanger (медленное колыхание — «течение») ──────────────────────
    def _flanger(self, seg: "AudioSegment") -> "AudioSegment":
        samples = np.array(seg.get_array_of_samples()).astype(np.float64)
        sr = seg.frame_rate
        n = len(samples)
        t = np.arange(n)
        # модулируемая задержка (в сэмплах)
        depth = (self.flanger_depth_ms / 1000.0) * sr
        lfo = (np.sin(2 * np.pi * self.flanger_rate_hz * t / sr) + 1) / 2
        delay = (lfo * depth).astype(np.int64)
        out = samples.copy()
        idx = t - delay
        valid = idx >= 0
        out[valid] = 0.6 * samples[valid] + 0.5 * samples[idx[valid]]
        out = np.clip(out, -32768, 32767).astype(np.int16)
        return seg._spawn(out.tobytes())

    # ── 4. Reverb (простой алгоритмический хвост эха) ─────────────────────
    def _reverb(self, seg: "AudioSegment") -> "AudioSegment":
        samples = np.array(seg.get_array_of_samples()).astype(np.float64)
        sr = seg.frame_rate
        delay_samples = int((self.reverb_ms / 1000.0) * sr)
        if delay_samples < 1:
            return seg
        out = samples.copy()
        # несколько затухающих отражений
        decay = self.reverb_decay
        for tap in range(1, 5):
            d = delay_samples * tap
            if d >= len(samples):
                break
            gain = decay ** tap
            out[d:] += samples[:-d] * gain
        # смешать wet/dry
        mixed = self.wet * out + (1 - self.wet) * samples
        mixed = np.clip(mixed, -32768, 32767).astype(np.int16)
        return seg._spawn(mixed.tobytes())


# ═══════════════════════════════════════════════════════════════════════════
#  VOICE OUTPUT
# ═══════════════════════════════════════════════════════════════════════════

class VoiceOutput:
    """
    Озвучивание ответов GIDEON «подводным» голосом.

    .env переменные (опционально):
      GIDEON_VOICE      — голос edge-tts (по умолчанию ru-RU-DmitryNeural)
      GIDEON_VOICE_RATE — скорость, напр. "-10%" (медленнее = атмосфернее)
      GIDEON_TTS        — "off" чтобы полностью отключить голос

    Голоса edge-tts (русские):
      ru-RU-DmitryNeural   — мужской, глубокий (рекомендуется для ядра)
      ru-RU-SvetlanaNeural — женский
    """

    def __init__(
        self,
        voice: str = "",
        rate: str = "",
        volume_db: float | None = None,
        enabled: bool = True,
        fx: "UnderwaterFX | None" = None,
    ) -> None:
        self.voice = voice or os.environ.get("GIDEON_VOICE", "ru-RU-DmitryNeural")
        self.rate = rate or os.environ.get("GIDEON_VOICE_RATE", "+12%")
        # Громкость в дБ (отрицательное = тише). По умолч. -8 дБ.
        if volume_db is None:
            volume_db = float(os.environ.get("GIDEON_VOICE_VOLUME", "-8"))
        self.volume_db = volume_db
        self.fx = fx or UnderwaterFX()

        # Кэш готовых (уже обработанных) аудио-сегментов по тексту фразы.
        # В памяти — для мгновенного повтора в рамках сессии.
        self._mem_cache: dict = {}
        # На диске — чтобы частые фразы не пересинтезировались после перезапуска.
        self._cache_dir = os.path.join(
            tempfile.gettempdir(), "gideon_voice_cache"
        )
        try:
            os.makedirs(self._cache_dir, exist_ok=True)
        except Exception:
            self._cache_dir = ""

        env_off = os.environ.get("GIDEON_TTS", "").lower() == "off"
        self.enabled = enabled and not env_off and _EDGE_AVAILABLE and _AUDIO_AVAILABLE

        if self.enabled:
            logger.info("VoiceOutput готов. Голос: %s (подводный режим)", self.voice)
        else:
            reason = ("отключён (.env)" if env_off else
                      "нет edge-tts/pydub" if not (_EDGE_AVAILABLE and _AUDIO_AVAILABLE)
                      else "выключен")
            logger.info("VoiceOutput неактивен: %s", reason)

    # ─── Публичный API ────────────────────────────────────────────────────

    async def speak_async(self, text: str) -> None:
        """
        Асинхронно озвучить текст (синтез + эффекты + воспроизведение).
        Защищено таймаутом — если сеть/звук зависли, Гидеон не залипает.
        """
        if not self.enabled or not text.strip():
            return
        try:
            await asyncio.wait_for(self._do_speak(text), timeout=20.0)
        except asyncio.TimeoutError:
            logger.warning("Озвучивание прервано по таймауту (сеть/звук)")
        except Exception as exc:
            logger.warning("Ошибка озвучивания: %s", exc)

    async def _do_speak(self, text: str) -> None:
        loop = asyncio.get_event_loop()
        key = self._cache_key(text)

        # ── Кэш в памяти — мгновенное воспроизведение ──────────────────
        seg = self._mem_cache.get(key)
        if seg is not None:
            logger.info("Голос из кэша (память): %r", text)
            await loop.run_in_executor(None, self._play_segment, seg)
            return

        # ── Кэш на диске — без обращения к edge-tts ────────────────────
        disk_path = self._disk_path(key)
        if disk_path and os.path.exists(disk_path):
            logger.info("Голос из кэша (диск): %r", text)
            seg = await loop.run_in_executor(
                None, lambda: AudioSegment.from_file(disk_path, format="wav")
            )
            self._mem_cache[key] = seg
            await loop.run_in_executor(None, self._play_segment, seg)
            return

        # ── Нет в кэше: синтез + обработка + сохранение ─────────────────
        mp3_bytes = await asyncio.wait_for(self._synthesize(text), timeout=10.0)
        if not mp3_bytes:
            logger.warning("edge-tts вернул пустое аудио")
            return

        # Обработать эффектами (в thread) и закэшировать
        seg = await loop.run_in_executor(None, self._render, mp3_bytes)
        self._mem_cache[key] = seg
        if disk_path:
            try:
                await loop.run_in_executor(
                    None, lambda: seg.export(disk_path, format="wav")
                )
            except Exception as exc:
                logger.debug("Не удалось сохранить кэш на диск: %s", exc)

        # Воспроизвести
        await loop.run_in_executor(None, self._play_segment, seg)

    def speak(self, text: str) -> None:
        """Синхронная версия — для запуска вне asyncio."""
        if not self.enabled or not text.strip():
            return
        try:
            mp3_bytes = asyncio.run(self._synthesize(text))
            self._process_and_play(mp3_bytes)
        except Exception as exc:
            logger.warning("Ошибка озвучивания: %s", exc)

    # ─── Внутреннее ───────────────────────────────────────────────────────

    async def _synthesize(self, text: str) -> bytes:
        """Синтезировать речь через edge-tts → mp3 bytes."""
        communicate = edge_tts.Communicate(text, self.voice, rate=self.rate)
        buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        return buf.getvalue()

    def _render(self, mp3_bytes: bytes) -> "AudioSegment":
        """Из mp3 → наложить эффекты + громкость → готовый сегмент."""
        seg = AudioSegment.from_file(io.BytesIO(mp3_bytes), format="mp3")
        seg = self.fx.apply(seg)
        if self.volume_db:
            seg = seg.apply_gain(self.volume_db)
        return seg

    def _play_segment(self, seg: "AudioSegment") -> None:
        """Воспроизвести готовый сегмент (блокирующий вызов)."""
        play(seg)

    def _process_and_play(self, mp3_bytes: bytes) -> None:
        """Совместимость: обработать и сразу воспроизвести."""
        self._play_segment(self._render(mp3_bytes))

    # ─── Кэш ───────────────────────────────────────────────────────────────

    def _cache_key(self, text: str) -> str:
        """Ключ кэша учитывает текст + параметры голоса (чтобы не путать)."""
        import hashlib
        raw = f"{text}|{self.voice}|{self.rate}|{self.volume_db}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _disk_path(self, key: str) -> str:
        if not self._cache_dir:
            return ""
        return os.path.join(self._cache_dir, key + ".wav")

    def preload(self, phrases: list) -> None:
        """
        Предзагрузить (синтезировать и закэшировать) список частых фраз.
        Вызывается в фоне при старте, чтобы первые команды звучали мгновенно.
        """
        if not self.enabled:
            return
        for text in phrases:
            if not text or not text.strip():
                continue
            key = self._cache_key(text)
            if key in self._mem_cache:
                continue
            disk_path = self._disk_path(key)
            try:
                if disk_path and os.path.exists(disk_path):
                    self._mem_cache[key] = AudioSegment.from_file(disk_path, format="wav")
                    continue
                mp3 = asyncio.run(self._synthesize(text))
                if not mp3:
                    continue
                seg = self._render(mp3)
                self._mem_cache[key] = seg
                if disk_path:
                    seg.export(disk_path, format="wav")
            except Exception as exc:
                logger.debug("preload %r не удался: %s", text, exc)
        logger.info("Голосовой кэш: предзагружено %d фраз", len(self._mem_cache))


# ═══════════════════════════════════════════════════════════════════════════
#  Самотест: python -m backend.voice_output
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    vo = VoiceOutput()
    print("Озвучиваю тестовую фразу...")
    vo.speak("Система Гидеон активирована. Готов к выполнению команд.")
    print("Готово.")
