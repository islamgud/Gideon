"""
backend/ai_brain.py
═══════════════════════════════════════════════════════════════════════════════
ИИ-мозг GIDEON — sentence-transformers (семантические эмбеддинги).

Как работает:
  1. При первом запуске скачивается модель paraphrase-multilingual-MiniLM-L12-v2
     (~500 МБ). Все последующие запуски — офлайн, из кэша.
  2. Для каждого намерения предвычисляются эмбеддинги всех примеров фраз.
  3. Запрос пользователя кодируется в вектор и сравнивается с примерами
     по косинусному сходству.
  4. Намерение с наибольшим сходством выбирается как ответ.
  5. Если сходство ниже порога — KeywordFallback.

Преимущества перед keyword-матчером:
  • Понимает смысл, а не ключевые слова:
    "хочу посмотреть видосики" → open_youtube
    "моя машина тормозит" → system_info
    "сделай потише" → volume_down
  • Работает с опечатками и перефразировками.
  • Мультиязычная модель — понимает русский и английский одновременно.
  • Полностью локально, без интернета (после первой загрузки).

Расширение:
  Добавить новую команду → добавить примеры в INTENT_DATASET
  и handler в INTENT_HANDLERS. Перезапустить — эмбеддинги пересчитаются.
"""

import asyncio
import logging
import re
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("gideon.ai_brain")


# ═══════════════════════════════════════════════════════════════════════════
#  ДАТАСЕТ НАМЕРЕНИЙ
#  Чем больше разнообразных примеров — тем точнее распознавание.
#  Модель понимает смысл, поэтому не нужно перечислять все варианты.
# ═══════════════════════════════════════════════════════════════════════════

INTENT_DATASET: dict[str, list[str]] = {

    "open_notepad": [
        "открой блокнот", "запусти блокнот", "блокнот", "notepad",
        "открой текстовый редактор", "мне нужен блокнот",
        "хочу написать текст", "создать текстовый файл",
        "нужен редактор текста", "открой textedit",
        "хочу что-то записать", "открой для записей",
    ],

    "open_browser": [
        "открой браузер", "запусти браузер", "браузер",
        "хочу в интернет", "открой интернет", "выйди в сеть",
        "открой хром", "открой firefox", "открой edge",
        "мне нужен браузер", "зайди в интернет",
    ],

    "open_calculator": [
        "открой калькулятор", "калькулятор", "calculator",
        "помоги посчитать", "нужно посчитать", "хочу посчитать",
        "запусти калькулятор", "нужны вычисления",
        "посчитай за меня", "мне нужен калькулятор",
    ],

    "open_youtube": [
        "открой ютуб", "зайди на ютуб", "ютуб", "youtube",
        "хочу смотреть видео", "включи ютуб", "хочу посмотреть видосики",
        "перейди на ютуб", "открой видео хостинг",
    ],

    "open_github": [
        "открой гитхаб", "зайди на гитхаб", "гитхаб", "github",
        "открой репозитории", "перейди на гитхаб",
        "хочу посмотреть код", "открой мои проекты",
    ],

    "open_vk": [
        "открой вконтакте", "вконтакте", "вк", "vk",
        "зайди в вк", "перейди в вконтакте", "хочу в вк",
        "открой социальную сеть", "вк.ком",
    ],

    "open_telegram": [
        "открой телеграм", "телеграм", "telegram",
        "зайди в телеграм", "перейди в мессенджер",
        "хочу написать в телеграм", "открой чаты",
    ],

    "open_google": [
        "открой гугл", "гугл", "google",
        "хочу погуглить", "найди в интернете", "открой поисковик",
        "зайди на гугл", "поищи в гугле",
    ],

    "open_explorer": [
        "открой проводник", "проводник", "explorer",
        "покажи файлы", "открой файловый менеджер",
        "хочу посмотреть файлы", "открой папки",
        "покажи мои документы", "открой мой компьютер",
    ],

    "open_task_manager": [
        "открой диспетчер задач", "диспетчер задач", "task manager",
        "что грузит компьютер", "посмотри процессы",
        "почему тормозит", "покажи загрузку процессора",
        "что жрёт память", "открой процессы",
    ],

    "open_control_panel": [
        "открой панель управления", "панель управления", "control panel",
        "открой настройки системы", "системные настройки",
        "хочу настроить систему", "параметры windows",
        "открой параметры", "настройки компьютера",
    ],

    "screenshot": [
        "сделай скриншот", "скриншот", "screenshot",
        "сфотографируй экран", "снимок экрана", "захвати экран",
        "сохрани то что на экране", "принтскрин", "printscreen",
        "запечатлей экран",
    ],

    "shutdown": [
        "выключи компьютер", "выключи пк", "завершить работу",
        "выключай систему", "shutdown", "пора выключать",
        "вырубай комп", "завершить сеанс", "выключи всё",
        "хватит работать выключай",
    ],

    "restart": [
        "перезагрузи компьютер", "перезагрузка", "restart", "reboot",
        "перезапусти систему", "перезагрузи пк",
        "нужна перезагрузка", "перезапусти комп",
        "комп завис перезагрузи", "ребут",
    ],

    "lock": [
        "заблокируй компьютер", "заблокируй экран", "lock screen",
        "заблокируй", "ухожу", "отойду от компьютера",
        "поставь блокировку", "заблокируй рабочий стол",
        "уходу поставь пароль", "хочу заблокировать экран",
    ],

    "volume_up": [
        "сделай громче", "прибавь звук", "громче",
        "увеличь громкость", "volume up", "звук погромче",
        "сделай погромче", "побольше звука",
        "не слышу сделай громче", "прибавь громкость",
    ],

    "volume_down": [
        "сделай тише", "убавь звук", "тише",
        "уменьши громкость", "volume down", "звук потише",
        "сделай потише", "слишком громко убавь",
        "громко убери немного", "убавь громкость",
    ],

    "volume_mute": [
        "выключи звук", "отключи звук", "mute",
        "замьютируй", "без звука", "убери звук",
        "замолчи", "тихий режим", "отключи аудио",
        "я на совещании выключи звук",
    ],

    "volume_unmute": [
        "включи звук", "unmute", "размьютируй",
        "верни звук", "включить аудио", "восстанови звук",
        "снова включи звук", "размьютируй микрофон",
    ],

    "system_info": [
        "информация о системе", "характеристики компьютера",
        "сколько памяти", "какой процессор", "какая ос",
        "что за система", "параметры системы", "покажи характеристики",
        "какое железо стоит", "моя машина тормозит покажи что происходит",
        "сколько оперативки", "какой у меня пк",
    ],

    "time_info": [
        "который час", "сколько времени", "текущее время",
        "какое время", "скажи время", "что за время сейчас",
        "какой сейчас час", "сколько часов на часах",
    ],

    "date_info": [
        "какое число", "какая дата", "текущая дата",
        "какой день сегодня", "какой месяц", "какой год",
        "скажи дату", "что за число сегодня",
    ],

    "greeting": [
        "привет", "здравствуй", "здравствуйте", "хай", "hi", "hello",
        "добрый день", "добрый вечер", "доброе утро",
        "салют", "йоу", "приветствую гидеон",
    ],

    "thanks": [
        "спасибо", "благодарю", "спасибо большое", "thanks", "thank you",
        "спс", "огромное спасибо", "ты молодец", "отлично справился",
        "хорошая работа", "спасибо помог",
    ],

    "help": [
        "что ты умеешь", "помощь", "help", "что можешь делать",
        "какие у тебя команды", "что ты знаешь",
        "расскажи о себе", "как тебя использовать",
        "какие команды поддерживаются",
    ],
}


# Маппинг intent → (tool_name, args) или строка для текстового ответа
INTENT_HANDLERS: dict[str, tuple[str, dict] | str] = {
    "open_notepad":       ("open_app",        {"app": "notepad"}),
    "open_browser":       ("open_app",        {"app": "browser"}),
    "open_calculator":    ("open_app",        {"app": "calculator"}),
    "open_youtube":       ("open_url",        {"url": "https://youtube.com"}),
    "open_github":        ("open_url",        {"url": "https://github.com"}),
    "open_vk":            ("open_url",        {"url": "https://vk.com"}),
    "open_telegram":      ("open_url",        {"url": "https://web.telegram.org"}),
    "open_google":        ("open_url",        {"url": "https://google.com"}),
    "open_explorer":      ("open_system_app", {"app": "explorer"}),
    "open_task_manager":  ("open_system_app", {"app": "taskmgr"}),
    "open_control_panel": ("open_system_app", {"app": "control"}),
    "screenshot":         ("take_screenshot", {}),
    "shutdown":           ("shutdown_pc",     {}),
    "restart":            ("restart_pc",      {}),
    "lock":               ("lock_pc",         {}),
    "volume_up":          ("set_volume",      {"action": "up"}),
    "volume_down":        ("set_volume",      {"action": "down"}),
    "volume_mute":        ("set_volume",      {"action": "mute"}),
    "volume_unmute":      ("set_volume",      {"action": "unmute"}),
    "system_info":        ("get_system_info", {"info_type": "all"}),
    "time_info":          ("get_system_info", {"info_type": "time"}),
    "date_info":          ("get_system_info", {"info_type": "time"}),
    # Текстовые ответы
    "greeting": "Привет! Чем могу помочь?",
    "thanks":   "Пожалуйста! Обращайся.",
    "help": (
        "Я умею: открывать приложения и сайты, делать скриншоты, "
        "управлять громкостью, показывать информацию о системе, "
        "выключать и перезагружать компьютер."
    ),
}


# ═══════════════════════════════════════════════════════════════════════════
#  KEYWORD FALLBACK — последний рубеж если модель не уверена
# ═══════════════════════════════════════════════════════════════════════════

class KeywordFallback:
    _RULES: list[tuple[list[str], str]] = [
        (["блокнот", "notepad"],                "open_notepad"),
        (["браузер", "browser", "интернет"],    "open_browser"),
        (["калькулятор", "calculator"],         "open_calculator"),
        (["ютуб", "youtube"],                   "open_youtube"),
        (["гитхаб", "github"],                  "open_github"),
        (["вконтакте", "вк", "vk"],             "open_vk"),
        (["телеграм", "telegram"],              "open_telegram"),
        (["гугл", "google"],                    "open_google"),
        (["проводник", "explorer"],             "open_explorer"),
        (["диспетчер задач", "task manager"],   "open_task_manager"),
        (["панель управления", "control panel"],"open_control_panel"),
        (["скриншот", "screenshot"],            "screenshot"),
        (["выключи компьютер", "shutdown"],     "shutdown"),
        (["перезагрузи", "restart", "reboot"],  "restart"),
        (["заблокируй", "lock"],                "lock"),
        (["громче", "прибавь звук"],            "volume_up"),
        (["тише", "убавь звук"],               "volume_down"),
        (["выключи звук", "mute"],              "volume_mute"),
        (["включи звук", "unmute"],             "volume_unmute"),
        (["характеристики", "сколько памяти"],  "system_info"),
        (["который час", "сколько времени"],    "time_info"),
        (["какое число", "какая дата"],         "date_info"),
        (["привет", "здравствуй", "hi"],        "greeting"),
        (["спасибо", "thanks"],                 "thanks"),
    ]

    def match(self, text: str) -> str | None:
        t = text.lower()
        for keywords, intent in self._RULES:
            if any(kw in t for kw in keywords):
                return intent
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  AI BRAIN
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class AIBrain:
    """
    ИИ-мозг GIDEON на базе sentence-transformers.

    Модель: paraphrase-multilingual-MiniLM-L12-v2
      • Мультиязычная (русский + английский и ещё 48 языков)
      • Размер: ~500 МБ (скачивается один раз)
      • Скорость: ~20-50 мс на запрос (CPU), ~5 мс (GPU)
      • Полностью офлайн после первой загрузки

    Атрибуты:
      confidence_threshold — минимальное косинусное сходство (0.0-1.0)
                             ниже порога → KeywordFallback
      model_name          — HuggingFace модель
    """

    confidence_threshold: float = 0.45
    model_name: str = "paraphrase-multilingual-MiniLM-L12-v2"

    # Внутреннее состояние
    _model:             Any  = field(default=None, init=False, repr=False)
    _intent_embeddings: dict = field(default_factory=dict, init=False)
    _intents:           list = field(default_factory=list, init=False)
    _fallback:          KeywordFallback = field(default_factory=KeywordFallback, init=False)
    _ready:             bool = field(default=False, init=False)
    _loading:           bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        # Загружаем модель в фоновом потоке — не блокируем старт приложения
        import threading
        t = threading.Thread(
            target=self._load_model,
            daemon=True,
            name="gideon-model-loader",
        )
        t.start()

    def _load_model(self) -> None:
        """Загрузка модели и предвычисление эмбеддингов (фоновый поток)."""
        self._loading = True
        try:
            from sentence_transformers import SentenceTransformer
            import numpy as np

            logger.info(
                "Загружаю модель %s... (первый раз ~500 МБ, потом из кэша)",
                self.model_name,
            )
            self._model = SentenceTransformer(self.model_name)

            # Предвычислить эмбеддинги для всех примеров в датасете
            logger.info("Вычисляю эмбеддинги для %d намерений...", len(INTENT_DATASET))
            self._intent_embeddings = {}
            self._intents = list(INTENT_DATASET.keys())

            for intent, phrases in INTENT_DATASET.items():
                embeddings = self._model.encode(
                    phrases,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                    batch_size=32,
                )
                self._intent_embeddings[intent] = embeddings

            self._ready = True
            logger.info(
                "AIBrain (sentence-transformers) готов. "
                "Намерений: %d, Примеров: %d",
                len(self._intents),
                sum(len(v) for v in INTENT_DATASET.values()),
            )

        except ImportError:
            logger.error(
                "sentence-transformers не установлен! "
                "Запусти: pip install sentence-transformers  "
                "Работаю в режиме KeywordFallback."
            )
        except Exception as exc:
            logger.exception("Ошибка загрузки модели: %s", exc)
        finally:
            self._loading = False

    # ─── Публичный API ────────────────────────────────────────────────────

    async def process(self, user_text: str, registry_execute) -> dict:
        """
        Классифицировать команду и выполнить handler.

        Если модель ещё загружается — используем KeywordFallback.
        Если модель готова — используем эмбеддинги.
        Если уверенность низкая — KeywordFallback.
        """
        if not self._ready:
            if self._loading:
                logger.info("Модель ещё загружается, использую KeywordFallback")
            intent = self._fallback.match(user_text)
            if intent:
                return await self._execute_intent(intent, registry_execute)
            return {"error": "Модель загружается, попробуй через несколько секунд"}

        # Классификация в executor — не блокируем event loop
        loop = asyncio.get_event_loop()
        intent, confidence = await loop.run_in_executor(
            None, self._classify, user_text
        )

        logger.info(
            "Классификация %r → %s (сходство: %.3f)",
            user_text, intent, confidence,
        )

        # Если уверенность ниже порога — KeywordFallback
        if confidence < self.confidence_threshold:
            logger.info(
                "Сходство %.3f < порога %.3f → KeywordFallback",
                confidence, self.confidence_threshold,
            )
            fb_intent = self._fallback.match(user_text)
            if fb_intent:
                return await self._execute_intent(fb_intent, registry_execute)
            return {"error": "Команда не распознана. Попробуй иначе."}

        return await self._execute_intent(intent, registry_execute)

    def clear_history(self) -> None:
        pass  # нет истории — каждый запрос независим

    @property
    def is_ai_active(self) -> bool:
        return self._ready

    # ─── Классификация ────────────────────────────────────────────────────

    def _classify(self, text: str) -> tuple[str, float]:
        """
        Найти ближайший intent по косинусному сходству.
        Возвращает (intent, max_similarity).
        """
        import numpy as np

        # Нормализованный эмбеддинг запроса
        query_emb = self._model.encode(
            [text],
            normalize_embeddings=True,
            show_progress_bar=False,
        )[0]

        best_intent = "unknown"
        best_score  = -1.0

        for intent, embeddings in self._intent_embeddings.items():
            # Косинусное сходство = скалярное произведение нормализованных векторов
            scores = embeddings @ query_emb
            # Берём максимальное сходство среди всех примеров намерения
            score = float(scores.max())
            if score > best_score:
                best_score  = score
                best_intent = intent

        return best_intent, best_score

    # ─── Выполнение intent ────────────────────────────────────────────────

    async def _execute_intent(self, intent: str, registry_execute) -> dict:
        handler = INTENT_HANDLERS.get(intent)

        if handler is None:
            return {"error": "Команда не распознана. Попробуй иначе."}

        # Текстовый ответ
        if isinstance(handler, str):
            return {"response": handler}

        # Вызов инструмента
        tool_name, args = handler
        try:
            return await registry_execute(tool_name, args)
        except Exception as exc:
            logger.exception("Ошибка выполнения %s", tool_name)
            return {"error": str(exc)}
