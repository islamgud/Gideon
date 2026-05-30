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
        """Асинхронно озвучить текст (синтез + эффекты + воспроизведение)."""
        if not self.enabled or not text.strip():
            return
        try:
            # 1. Синтез через edge-tts (async)
            mp3_bytes = await self._synthesize(text)
            # 2. Обработка эффектами + воспроизведение в thread (блокирующее)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._process_and_play, mp3_bytes)
        except Exception as exc:
            logger.warning("Ошибка озвучивания: %s", exc)

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

    def _process_and_play(self, mp3_bytes: bytes) -> None:
        """Наложить эффекты и воспроизвести (блокирующий вызов)."""
        seg = AudioSegment.from_file(io.BytesIO(mp3_bytes), format="mp3")
        seg = self.fx.apply(seg)
        # Регулировка громкости (тише по умолчанию)
        if self.volume_db:
            seg = seg.apply_gain(self.volume_db)
        play(seg)


# ═══════════════════════════════════════════════════════════════════════════
#  Самотест: python -m backend.voice_output
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    vo = VoiceOutput()
    print("Озвучиваю тестовую фразу...")
    vo.speak("Система Гидеон активирована. Готов к выполнению команд.")
    print("Готово.")
