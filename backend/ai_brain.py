"""
backend/ai_brain.py
═══════════════════════════════════════════════════════════════════════════════
ИИ-мозг GIDEON — Groq LLM → JSON команда → Python executor.

Архитектура:
  1. Пользователь говорит что угодно
  2. Groq LLM понимает смысл и возвращает JSON:
     {"action": "open_url", "value": "https://speedtest.net"}
  3. Python executor выполняет действие

Что хардкодим — только ДЕЙСТВИЯ (actions):
  open_url        — открыть любой сайт
  open_app        — открыть любое приложение
  open_folder     — открыть папку
  system_command  — системная команда (shutdown, restart, lock, volume, screenshot)
  get_info        — получить информацию (время, дата, характеристики)
  answer          — просто ответить текстом (если не команда)

Что НЕ хардкодим:
  — конкретные сайты
  — названия программ
  — вариации слов

Fallback:
  Если GROQ_API_KEY не задан — KeywordFallback.
"""

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from .memory import Memory

logger = logging.getLogger("gideon.ai_brain")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from groq import Groq
    _GROQ_AVAILABLE = True
except ImportError:
    _GROQ_AVAILABLE = False
    logger.warning("groq не установлен. pip install groq")


# ═══════════════════════════════════════════════════════════════════════════
#  СИСТЕМНЫЙ ПРОМПТ
# ═══════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """Ты — ИИ-ядро голосового ассистента GIDEON для Windows.

Твоя ЕДИНСТВЕННАЯ задача — преобразовать команду пользователя в JSON.
Ты НИКОГДА не отвечаешь текстом. ТОЛЬКО JSON. Без markdown, без пояснений.

Доступные действия (actions):

1. open_url — открыть сайт в браузере
   {"action": "open_url", "value": "https://..."}

2. open_app — открыть приложение
   {"action": "open_app", "value": "название_программы"}
   Значения value: notepad, calculator, chrome, firefox, edge, discord,
   steam, spotify, vscode, word, excel, explorer, taskmgr, control,
   powershell, cmd, paint, winamp — или любое другое название exe

3. open_folder — открыть папку
   {"action": "open_folder", "value": "путь_или_название"}
   Значения value: downloads, documents, desktop, pictures, music,
   videos — или полный путь типа C:\\Users\\...

4. system_command — системная команда
   {"action": "system_command", "value": "команда", "param": "..."}
   Значения value:
     shutdown, restart, lock, screenshot,
     volume_up, volume_down, volume_mute, volume_unmute,
     volume_set (param = число 0-100),
     volume_delta_up (param = число), volume_delta_down (param = число)

5. get_info — получить информацию
   {"action": "get_info", "value": "тип"}
   Значения value: time, date, cpu, memory, disk, os, all

6. change_language — сменить язык системы или раскладку клавиатуры
   {"action": "change_language", "value": "язык"}
   Значения value: en, ru, de, fr, zh, ja, ko, ar, tr — код языка ISO 639-1

7. remember — запомнить факт о пользователе (имя, предпочтения и т.д.)
   {"action": "remember", "value": "ключ", "param": "значение"}

8. recall — вспомнить ранее запомненный факт
   {"action": "recall", "value": "ключ"}

9. forget — забыть факт
   {"action": "forget", "value": "ключ"}

10. answer — ответить текстом (только если это не команда)
   {"action": "answer", "value": "текст ответа"}

Примеры:

"открой ютуб" → {"action": "open_url", "value": "https://youtube.com"}
"открой сайт спидтест" → {"action": "open_url", "value": "https://www.speedtest.net"}
"открой файрфокс" → {"action": "open_app", "value": "firefox"}
"открой дискорд" → {"action": "open_app", "value": "discord"}
"открой дс" → {"action": "open_app", "value": "discord"}
"открой загрузки" → {"action": "open_folder", "value": "downloads"}
"открой документы" → {"action": "open_folder", "value": "documents"}
"выключи комп" → {"action": "system_command", "value": "shutdown"}
"прибавь громкость на 15" → {"action": "system_command", "value": "volume_delta_up", "param": "15"}
"поставь громкость 50" → {"action": "system_command", "value": "volume_set", "param": "50"}
"который час" → {"action": "get_info", "value": "time"}
"привет" → {"action": "answer", "value": "Привет! Чем могу помочь?"}
"покажи настройки wifi" → {"action": "system_command", "value": "ncpa.cpl"}
"открой параметры" → {"action": "system_command", "value": "ms-settings:"}
"открой параметры windows" → {"action": "system_command", "value": "ms-settings:"}
"открой персонализацию" → {"action": "system_command", "value": "ms-settings:personalization"}
"открой обои" → {"action": "system_command", "value": "ms-settings:personalization-background"}
"открой bluetooth" → {"action": "system_command", "value": "ms-settings:bluetooth"}
"открой звук" → {"action": "system_command", "value": "ms-settings:sound"}
"открой дисплей" → {"action": "system_command", "value": "ms-settings:display"}
"открой обновления" → {"action": "system_command", "value": "ms-settings:windowsupdate"}
"открой приложения" → {"action": "system_command", "value": "ms-settings:appsfeatures"}
"открой уведомления" → {"action": "system_command", "value": "ms-settings:notifications"}
"открой учётные записи" → {"action": "system_command", "value": "ms-settings:accounts"}
"открой конфиденциальность" → {"action": "system_command", "value": "ms-settings:privacy"}
"открой сеть" → {"action": "system_command", "value": "ms-settings:network"}
"открой диспетчер задач" → {"action": "system_command", "value": "taskmgr"}
"открой панель управления" → {"action": "system_command", "value": "control"}
"открой реестр" → {"action": "system_command", "value": "regedit"}
"открой службы" → {"action": "system_command", "value": "services.msc"}
"открой диспетчер устройств" → {"action": "system_command", "value": "devmgmt.msc"}
"поставь английский язык" → {"action": "change_language", "value": "en"}
"смени язык на русский" → {"action": "change_language", "value": "ru"}
"поменяй язык на немецкий" → {"action": "change_language", "value": "de"}
"переключи раскладку на английский" → {"action": "change_language", "value": "en"}
"запомни меня зовут Ислам" → {"action": "remember", "value": "имя", "param": "Ислам"}
"запомни что мой любимый цвет синий" → {"action": "remember", "value": "любимый цвет", "param": "синий"}
"как меня зовут" → {"action": "recall", "value": "имя"}
"какой мой любимый цвет" → {"action": "recall", "value": "любимый цвет"}
"забудь как меня зовут" → {"action": "forget", "value": "имя"}

Если в разделе «Известные факты» есть нужная информация — используй
action "answer" и ответь по памяти естественной фразой
(например на «как меня зовут» при факте «имя: Ислам» → answer «Тебя зовут Ислам»).

Отвечай ТОЛЬКО валидным JSON. Никакого текста вокруг."""


# ═══════════════════════════════════════════════════════════════════════════
#  КАТАЛОГ КОМАНД — для окна «Список команд» во frontend
#  Это примеры того, что понимает GIDEON. Благодаря LLM реальных
#  формулировок намного больше — список лишь демонстрирует возможности.
# ═══════════════════════════════════════════════════════════════════════════

COMMAND_CATALOG = [
    # Приложения
    {"name": "открой блокнот",            "description": "Запустить текстовый редактор"},
    {"name": "открой калькулятор",        "description": "Запустить калькулятор"},
    {"name": "открой браузер",            "description": "Запустить браузер по умолчанию"},
    {"name": "открой дискорд",            "description": "Запустить Discord"},
    {"name": "открой стим",               "description": "Запустить Steam"},
    {"name": "открой проводник",          "description": "Открыть файловый менеджер"},
    {"name": "открой диспетчер задач",    "description": "Открыть Task Manager"},
    # Сайты
    {"name": "открой ютуб",               "description": "Открыть YouTube в браузере"},
    {"name": "открой гитхаб",             "description": "Открыть GitHub"},
    {"name": "открой гугл",               "description": "Открыть поиск Google"},
    {"name": "открой сайт спидтест",      "description": "Открыть нужный сайт по названию"},
    # Папки
    {"name": "открой загрузки",           "description": "Открыть папку «Загрузки»"},
    {"name": "открой документы",          "description": "Открыть папку «Документы»"},
    {"name": "открой рабочий стол",       "description": "Открыть рабочий стол"},
    # Громкость
    {"name": "прибавь громкость на 10",   "description": "Увеличить громкость на 10%"},
    {"name": "убавь громкость на 20",     "description": "Уменьшить громкость на 20%"},
    {"name": "поставь громкость 50",      "description": "Установить громкость 50%"},
    {"name": "выключи звук",              "description": "Отключить звук (mute)"},
    {"name": "включи звук",               "description": "Включить звук (unmute)"},
    # Система
    {"name": "сделай скриншот",           "description": "Снимок экрана на рабочий стол"},
    {"name": "заблокируй экран",          "description": "Заблокировать рабочую станцию"},
    {"name": "перезагрузи компьютер",     "description": "Перезагрузка ПК"},
    {"name": "выключи компьютер",         "description": "Выключение ПК"},
    # Настройки Windows
    {"name": "открой параметры",          "description": "Параметры Windows"},
    {"name": "открой панель управления",  "description": "Панель управления"},
    {"name": "открой персонализацию",     "description": "Настройки персонализации"},
    {"name": "открой bluetooth",          "description": "Настройки Bluetooth"},
    {"name": "открой настройки звука",    "description": "Настройки звука"},
    {"name": "открой настройки wifi",     "description": "Сетевые подключения"},
    # Язык
    {"name": "поставь английский язык",   "description": "Сменить язык ввода на английский"},
    {"name": "смени язык на русский",     "description": "Сменить язык ввода на русский"},
    # Информация / диалог
    {"name": "который час",               "description": "Текущее время"},
    {"name": "какое число",               "description": "Текущая дата"},
    {"name": "характеристики компьютера", "description": "ОС, процессор, память, диск"},
]


# ═══════════════════════════════════════════════════════════════════════════
#  EXECUTOR — выполняет JSON команды
# ═══════════════════════════════════════════════════════════════════════════

class CommandExecutor:
    """
    Выполняет JSON команды от LLM.
    Хардкодим только действия — не конкретные значения.
    """

    def __init__(self, memory=None) -> None:
        self.memory = memory

    async def execute(self, cmd: dict, registry_execute) -> dict:
        action = cmd.get("action", "")
        value  = cmd.get("value", "")
        param  = cmd.get("param", "")

        logger.info("Executor: action=%s value=%s param=%s", action, value, param)

        if action == "open_url":
            return await self._open_url(value, registry_execute)

        elif action == "open_app":
            return await self._open_app(value, registry_execute)

        elif action == "open_folder":
            return await self._open_folder(value, registry_execute)

        elif action == "system_command":
            return await self._system_command(value, param, registry_execute)

        elif action == "get_info":
            return await registry_execute("get_system_info", {"info_type": value or "all"})

        elif action == "change_language":
            return await self._change_language(value, registry_execute)

        elif action == "remember":
            return self._remember(value, param)

        elif action == "recall":
            return self._recall(value)

        elif action == "forget":
            return self._forget(value)

        elif action == "answer":
            return {"response": value}

        else:
            return {"error": f"Неизвестное действие: {action}"}

    # ─── Память ────────────────────────────────────────────────────────────

    def _remember(self, key: str, value: str) -> dict:
        if not self.memory:
            return {"error": "Память недоступна"}
        if not key or not value:
            return {"error": "Не понял что запомнить"}
        self.memory.remember(key, value)
        return {"response": "Запомнил"}

    def _recall(self, key: str) -> dict:
        if not self.memory:
            return {"error": "Память недоступна"}
        val = self.memory.recall(key)
        if val is None:
            # пробуем поиск по подстроке
            found = self.memory.search(key)
            if found:
                val = next(iter(found.values()))
        if val is None:
            return {"response": "Не помню такого"}
        return {"response": val}

    def _forget(self, key: str) -> dict:
        if not self.memory:
            return {"error": "Память недоступна"}
        ok = self.memory.forget(key)
        return {"response": "Забыл" if ok else "Такого и не помнил"}

    async def _change_language(self, lang: str, registry_execute) -> dict:
        """
        Сменить язык ввода/раскладку клавиатуры через PowerShell.
        lang: код ISO 639-1 (en, ru, de, fr, ...)
        """
        import subprocess

        # Маппинг кода языка → Windows culture code
        LANG_MAP = {
            "en": "en-US",
            "ru": "ru-RU",
            "de": "de-DE",
            "fr": "fr-FR",
            "zh": "zh-CN",
            "ja": "ja-JP",
            "ko": "ko-KR",
            "ar": "ar-SA",
            "tr": "tr-TR",
            "uk": "uk-UA",
            "pl": "pl-PL",
            "es": "es-ES",
            "it": "it-IT",
        }

        LANG_NAMES = {
            "en": "английский",
            "ru": "русский",
            "de": "немецкий",
            "fr": "французский",
            "zh": "китайский",
            "ja": "японский",
            "ko": "корейский",
            "ar": "арабский",
            "tr": "турецкий",
            "uk": "украинский",
            "pl": "польский",
            "es": "испанский",
            "it": "итальянский",
        }

        culture = LANG_MAP.get(lang.lower())
        if not culture:
            return {"error": f"Неизвестный язык: {lang}"}

        # PowerShell скрипт — устанавливает язык ввода
        ps_script = f"""
$lang = "{culture}"
$langList = Get-WinUserLanguageList
$exists = $langList | Where-Object {{ $_.LanguageTag -eq $lang }}
if (-not $exists) {{
    $langList.Add($lang)
    Set-WinUserLanguageList $langList -Force
}}
# Переместить нужный язык на первое место
$sorted = @($langList | Where-Object {{ $_.LanguageTag -eq $lang }}) + 
          @($langList | Where-Object {{ $_.LanguageTag -ne $lang }})
Set-WinUserLanguageList $sorted -Force
"""
        try:
            result = subprocess.run(
                ["powershell", "-NonInteractive", "-Command", ps_script],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                name = LANG_NAMES.get(lang.lower(), lang)
                return {"response": f"Язык изменён на {name}"}
            else:
                # Fallback — просто открываем настройки языка
                subprocess.Popen(["start", "ms-settings:language"], shell=True)
                return {"response": "Открываю настройки языка"}
        except Exception as exc:
            subprocess.Popen(["start", "ms-settings:language"], shell=True)
            return {"response": "Открываю настройки языка"}

    async def _open_url(self, url: str, registry_execute) -> dict:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        return await registry_execute("open_url", {"url": url})

    async def _open_app(self, app: str, registry_execute) -> dict:
        # Маппинг популярных имён → exe
        APP_MAP = {
            "chrome":    "chrome",
            "firefox":   "firefox",
            "edge":      "msedge",
            "discord":   "discord",
            "дискорд":   "discord",
            "дс":        "discord",
            "steam":     "steam",
            "spotify":   "spotify",
            "vscode":    "code",
            "vs code":   "code",
            "word":      "winword",
            "excel":     "excel",
            "explorer":  "explorer",
            "проводник": "explorer",
            "taskmgr":   "taskmgr",
            "диспетчер": "taskmgr",
            "control":   "control",
            "powershell":"powershell",
            "cmd":       "cmd",
            "paint":     "mspaint",
            "notepad":   "notepad",
            "блокнот":   "notepad",
            "calculator":"calc",
            "калькулятор":"calc",
            "telegram":  "telegram",
            "телеграм":  "telegram",
            "obs":       "obs64",
            "vlc":       "vlc",
            "zoom":      "zoom",
        }
        exe = APP_MAP.get(app.lower(), app)

        # Сначала пробуем через наш registry (встроенные приложения)
        known = {"notepad": "notepad", "calculator": "calculator",
                 "calc": "calculator", "блокнот": "notepad"}
        if app.lower() in known or exe in known.values():
            app_key = known.get(app.lower(), known.get(exe, ""))
            if app_key:
                return await registry_execute("open_app", {"app": app_key})

        # Браузеры: открываем с домашней страницей (не форсим URL).
        # "start chrome" без аргументов → откроется домашняя страница браузера.
        import subprocess
        BROWSER_EXE = {
            "chrome": "chrome", "хром": "chrome",
            "firefox": "firefox", "файрфокс": "firefox",
            "edge": "msedge", "msedge": "msedge",
            "браузер": "", "browser": "",  # пустой → браузер по умолчанию
        }
        if app.lower() in BROWSER_EXE or exe in BROWSER_EXE.values():
            target = BROWSER_EXE.get(app.lower(), exe)
            try:
                if target:
                    # Конкретный браузер по имени (chrome/firefox/msedge).
                    # start резолвит путь из App Paths; без URL → домашняя страница.
                    subprocess.Popen(f'start "" {target}', shell=True)
                else:
                    # «браузер» без уточнения → браузер по умолчанию.
                    # Запуск пустого start открывает ассоциированное приложение.
                    subprocess.Popen('start ""', shell=True)
                return {"response": "Открываю браузер"}
            except Exception:
                pass

        # Остальные приложения: пробуем несколько способов запуска по очереди.
        import subprocess
        # 1) через shell "start" — резолвит App Paths из реестра (chrome, discord и т.д.)
        # 2) напрямую по имени exe
        attempts = [
            f'start "" "{exe}"',   # оболочка ищет в App Paths
            f'start "" {exe}',
        ]
        for cmd in attempts:
            try:
                subprocess.Popen(cmd, shell=True)
                return {"response": f"Открываю {app}"}
            except Exception:
                continue
        # 3) последняя попытка — напрямую
        try:
            subprocess.Popen([exe], shell=False)
            return {"response": f"Открываю {app}"}
        except Exception as exc:
            return {"error": f"Не удалось открыть {app}. Возможно, приложение не установлено."}

    async def _open_folder(self, folder: str, registry_execute) -> dict:
        import subprocess, os
        FOLDER_MAP = {
            "downloads":  os.path.join(os.path.expanduser("~"), "Downloads"),
            "загрузки":   os.path.join(os.path.expanduser("~"), "Downloads"),
            "documents":  os.path.join(os.path.expanduser("~"), "Documents"),
            "документы":  os.path.join(os.path.expanduser("~"), "Documents"),
            "desktop":    os.path.join(os.path.expanduser("~"), "Desktop"),
            "рабочий стол": os.path.join(os.path.expanduser("~"), "Desktop"),
            "pictures":   os.path.join(os.path.expanduser("~"), "Pictures"),
            "картинки":   os.path.join(os.path.expanduser("~"), "Pictures"),
            "music":      os.path.join(os.path.expanduser("~"), "Music"),
            "музыка":     os.path.join(os.path.expanduser("~"), "Music"),
            "videos":     os.path.join(os.path.expanduser("~"), "Videos"),
            "видео":      os.path.join(os.path.expanduser("~"), "Videos"),
        }
        path = FOLDER_MAP.get(folder.lower(), folder)
        try:
            subprocess.Popen(["explorer", path])
            return {"response": f"Открываю {folder}"}
        except Exception as exc:
            return {"error": str(exc)}

    async def _system_command(self, value: str, param: str,
                               registry_execute) -> dict:
        SYSTEM_MAP = {
            "shutdown":          ("shutdown_pc",     {}),
            "restart":           ("restart_pc",      {}),
            "lock":              ("lock_pc",         {}),
            "screenshot":        ("take_screenshot", {}),
            "volume_up":         ("set_volume",      {"action": "up"}),
            "volume_down":       ("set_volume",      {"action": "down"}),
            "volume_mute":       ("set_volume",      {"action": "mute"}),
            "volume_unmute":     ("set_volume",      {"action": "unmute"}),
        }

        if value == "volume_set" and param:
            return await registry_execute(
                "set_volume", {"action": "set", "level": int(param)}
            )

        if value == "volume_delta_up" and param:
            return await registry_execute(
                "set_volume", {"action": "up", "delta": int(param)}
            )

        if value == "volume_delta_down" and param:
            return await registry_execute(
                "set_volume", {"action": "down", "delta": int(param)}
            )

        mapping = SYSTEM_MAP.get(value)
        if mapping:
            tool_name, args = mapping
            return await registry_execute(tool_name, args)

        # ms-settings: — современные параметры Windows 10/11
        import subprocess
        if value.startswith("ms-settings:"):
            try:
                subprocess.Popen(["start", value], shell=True)
                return {"response": f"Открываю настройки"}
            except Exception as exc:
                return {"error": str(exc)}

        # .msc / .cpl — системные оснастки
        if value.endswith((".msc", ".cpl")):
            try:
                subprocess.Popen(["start", value], shell=True)
                return {"response": f"Открываю {value}"}
            except Exception as exc:
                return {"error": str(exc)}

        # taskmgr, regedit, control и другие exe
        try:
            subprocess.Popen([value], shell=True)
            return {"response": f"Выполняю: {value}"}
        except Exception as exc:
            return {"error": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
#  KEYWORD FALLBACK
# ═══════════════════════════════════════════════════════════════════════════

class KeywordFallback:
    _RULES = [
        (["блокнот", "notepad"],              "open_app",     {"app": "notepad"}),
        (["браузер", "browser", "интернет"],  "open_app",     {"app": "browser"}),
        (["калькулятор", "calculator"],       "open_app",     {"app": "calculator"}),
        (["ютуб", "youtube"],                 "open_url",     {"url": "https://youtube.com"}),
        (["гитхаб", "github"],                "open_url",     {"url": "https://github.com"}),
        (["вк", "vk", "вконтакте"],           "open_url",     {"url": "https://vk.com"}),
        (["телеграм", "telegram"],            "open_url",     {"url": "https://web.telegram.org"}),
        (["гугл", "google"],                  "open_url",     {"url": "https://google.com"}),
        (["проводник", "explorer"],           "open_system_app", {"app": "explorer"}),
        (["диспетчер задач"],                 "open_system_app", {"app": "taskmgr"}),
        (["панель управления"],               "open_system_app", {"app": "control"}),
        (["скриншот", "screenshot"],          "take_screenshot", {}),
        (["выключи компьютер"],               "shutdown_pc",  {}),
        (["перезагрузи"],                     "restart_pc",   {}),
        (["заблокируй"],                      "lock_pc",      {}),
        (["громче"],                          "set_volume",   {"action": "up"}),
        (["тише"],                            "set_volume",   {"action": "down"}),
        (["выключи звук", "mute"],            "set_volume",   {"action": "mute"}),
        (["который час", "время"],            "get_system_info", {"info_type": "time"}),
        (["характеристики", "сколько памяти"],"get_system_info", {"info_type": "all"}),
    ]

    def match(self, text: str) -> tuple | None:
        t = text.lower()
        for keywords, tool, args in self._RULES:
            if any(kw in t for kw in keywords):
                return tool, args
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  AI BRAIN
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class AIBrain:
    """
    ИИ-мозг GIDEON.
    Groq LLM → JSON → CommandExecutor → результат.
    """

    model:      str = ""
    max_tokens: int = 150    # JSON короткий, много не нужно

    _DEFAULT_MODEL = "llama-3.3-70b-versatile"

    _client:   Any             = field(default=None, init=False, repr=False)
    _executor: CommandExecutor = field(default=None, init=False, repr=False)
    memory:    Any             = field(default=None, init=False, repr=False)
    _fallback: KeywordFallback = field(default_factory=KeywordFallback, init=False)
    _api_ok:   bool            = field(default=False, init=False)

    def __post_init__(self) -> None:
        # Долговременная память + исполнитель с доступом к ней
        self.memory = Memory()
        self._executor = CommandExecutor(memory=self.memory)

        if not self.model:
            self.model = os.environ.get("GROQ_MODEL", self._DEFAULT_MODEL)

        api_key = os.environ.get("GROQ_API_KEY", "").strip()
        if not api_key:
            logger.warning(
                "GROQ_API_KEY не найден. Работаю в режиме KeywordFallback. "
                "Получи ключ: https://console.groq.com"
            )
            return

        if not _GROQ_AVAILABLE:
            logger.warning("groq SDK не установлен. pip install groq")
            return

        try:
            self._client = Groq(api_key=api_key)
            self._api_ok = True
            logger.info("AIBrain (Groq) готов. Модель: %s", self.model)
        except Exception as exc:
            logger.error("Ошибка инициализации Groq: %s", exc)

    async def process(self, user_text: str, registry_execute) -> dict:
        """Старый путь (сразу выполнить). Оставлен для совместимости."""
        cmd = await self.plan(user_text)
        return await self.execute_plan(cmd, registry_execute)

    async def plan(self, user_text: str) -> dict:
        """
        Фаза 1: получить от LLM команду (JSON), НЕ выполняя её.
        Возвращает dict вида {"action": ..., "value": ..., "param": ...}
        либо {"action": "error", "value": "..."} при сбое.
        """
        if not self._api_ok or self._client is None:
            return self._fallback_plan(user_text)
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._groq_plan(user_text)
            )
        except Exception:
            logger.exception("Ошибка Groq при планировании, fallback")
            return self._fallback_plan(user_text)

    async def execute_plan(self, cmd: dict, registry_execute) -> dict:
        """Фаза 2: выполнить уже полученную команду."""
        if cmd.get("action") == "error":
            return {"error": cmd.get("value", "Команда не распознана")}
        return await self._executor.execute(cmd, registry_execute)

    @staticmethod
    def speech_for(cmd: dict) -> str:
        """
        Короткая фраза, которую Гидеон произносит ДО выполнения команды.
        Для action=answer фраза не нужна (там сам ответ и есть речь).
        """
        action = cmd.get("action", "")
        value  = (cmd.get("value", "") or "").lower()

        if action == "answer":
            return ""  # ответ озвучивается как есть, отдельная фраза не нужна

        if action == "open_url":
            return "Открываю сайт"
        if action == "open_folder":
            names = {"downloads": "папку загрузки", "загрузки": "папку загрузки",
                     "documents": "документы", "документы": "документы",
                     "desktop": "рабочий стол", "рабочий стол": "рабочий стол",
                     "pictures": "картинки", "music": "музыку", "videos": "видео"}
            return f"Открываю {names.get(value, 'папку')}"
        if action == "open_app":
            browsers = {"chrome", "firefox", "msedge", "edge", "браузер", "browser"}
            if value in browsers:
                return "Открываю браузер"
            return f"Открываю {value}" if value else "Открываю приложение"
        if action == "change_language":
            return "Меняю язык"
        if action == "get_info":
            return "Секунду"
        if action == "system_command":
            phrases = {
                "shutdown": "Выключаю компьютер", "restart": "Перезагружаю компьютер",
                "lock": "Блокирую экран", "screenshot": "Делаю скриншот",
                "volume_up": "Прибавляю громкость", "volume_down": "Убавляю громкость",
                "volume_mute": "Выключаю звук", "volume_unmute": "Включаю звук",
                "volume_set": "Меняю громкость",
                "volume_delta_up": "Прибавляю громкость",
                "volume_delta_down": "Убавляю громкость",
            }
            return phrases.get(value, "Выполняю")
        return "Выполняю"

    def clear_history(self) -> None:
        pass

    @property
    def is_ai_active(self) -> bool:
        return self._api_ok and self._client is not None

    def get_commands(self) -> list:
        """
        Каталог команд для окна «Список команд» во frontend.
        Формат: [{"name": "...", "description": "..."}].
        """
        return COMMAND_CATALOG

    # ─── Groq вызов ───────────────────────────────────────────────────────

    def _groq_plan(self, user_text: str) -> dict:
        """Запрос к Groq → распарсенный JSON-план (без выполнения)."""
        # Подмешиваем известные факты из памяти в системный промпт,
        # чтобы Гидеон отвечал с их учётом.
        system = SYSTEM_PROMPT
        if self.memory:
            facts = self.memory.context_string()
            if facts:
                system = SYSTEM_PROMPT + "\n\n" + facts

        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system",  "content": system},
                {"role": "user",    "content": user_text},
            ],
            max_tokens=self.max_tokens,
            temperature=0.1,
        )
        raw = response.choices[0].message.content or ""
        logger.info("Groq ответил: %s", raw.strip())

        cmd = self._parse_json(raw)
        if cmd is None:
            logger.warning("Не удалось распарсить JSON: %s", raw)
            return {"action": "error", "value": "Не понял команду"}
        return cmd

    @staticmethod
    def _parse_json(text: str) -> dict | None:
        """Извлечь JSON из ответа модели."""
        text = text.strip()
        # Убрать markdown-обёртку если есть
        text = re.sub(r"```(?:json)?", "", text).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Попробовать найти JSON внутри текста
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
        return None

    def _fallback_plan(self, text: str) -> dict:
        """Keyword-fallback → JSON-план в том же формате что у LLM."""
        match = self._fallback.match(text)
        if not match:
            return {"action": "error", "value": "Команда не распознана. Попробуй иначе."}
        tool_name, args = match
        logger.info("[Fallback] %s(%s)", tool_name, args)
        # Конвертируем (tool, args) → формат плана executor'а
        if tool_name == "open_app":
            return {"action": "open_app", "value": args.get("app", "")}
        if tool_name == "open_url":
            return {"action": "open_url", "value": args.get("url", "")}
        if tool_name == "open_system_app":
            return {"action": "system_command", "value": args.get("app", "")}
        if tool_name == "take_screenshot":
            return {"action": "system_command", "value": "screenshot"}
        if tool_name == "shutdown_pc":
            return {"action": "system_command", "value": "shutdown"}
        if tool_name == "restart_pc":
            return {"action": "system_command", "value": "restart"}
        if tool_name == "lock_pc":
            return {"action": "system_command", "value": "lock"}
        if tool_name == "set_volume":
            return {"action": "system_command", "value": "volume_" + args.get("action", "up")}
        if tool_name == "get_system_info":
            return {"action": "get_info", "value": args.get("info_type", "all")}
        return {"action": "error", "value": "Команда не распознана"}
