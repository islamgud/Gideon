"""
backend/command_processor.py
Процессор текстовых команд.

Архитектура:
  CommandProcessor хранит словарь паттернов → handler.
  Каждый handler — это (tool_name, args_dict) или callable.
  CommandProcessor.execute() нормализует текст,
  находит совпадение и возвращает результат через tools.registry.

Расширение:
  processor.register_command(keywords, tool_name, args)
  или
  processor.register_command(keywords, handler_fn)
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Awaitable

from .tools import registry

logger = logging.getLogger("gideon.command_processor")


# ── Тип обработчика команды ──────────────────────────────────────────────
CommandHandler = Callable[[str], Awaitable[dict]]


@dataclass
class CommandRule:
    """Одно правило: набор ключевых слов + handler."""
    keywords: list[str]           # любое из этих слов активирует правило
    handler:  CommandHandler      # async fn(normalized_text) -> dict


class CommandProcessor:
    """
    Диспетчер текстовых команд.

    Методы:
      register_command()  — добавить новое правило
      execute()           — обработать команду
      normalize_command() — нормализация текста перед поиском
    """

    def __init__(self) -> None:
        self._rules: list[CommandRule] = []
        self._register_defaults()

    # ─── Публичный API ───────────────────────────────────────────────────

    def register_command(
        self,
        keywords: list[str],
        tool_name: str | None = None,
        args: dict | None = None,
        handler: CommandHandler | None = None,
    ) -> None:
        """
        Зарегистрировать команду.

        Варианты вызова:
          1. register_command(["открой", "запусти"], tool_name="open_app", args={"app": "notepad"})
          2. register_command(["погода"], handler=my_async_fn)
        """
        if handler is None:
            if tool_name is None:
                raise ValueError("Нужен tool_name или handler")
            # Замыкание сохраняет значения tool_name и args
            _tool = tool_name
            _args = args or {}

            async def _tool_handler(_text: str) -> dict:
                return await registry.execute(_tool, _args)

            handler = _tool_handler

        self._rules.append(CommandRule(keywords=keywords, handler=handler))
        logger.info("Зарегистрирована команда: %s", keywords)

    async def execute(self, text: str) -> dict:
        """
        Нормализовать текст и найти первое совпадающее правило.
        Возвращает dict с ключом 'response' или 'error'.
        """
        normalized = self.normalize_command(text)
        logger.info("Команда (normalized): %r", normalized)

        for rule in self._rules:
            if any(kw in normalized for kw in rule.keywords):
                try:
                    return await rule.handler(normalized)
                except Exception as exc:
                    logger.exception("Ошибка обработчика команды")
                    return {"error": str(exc)}

        return {"error": "Команда не распознана"}

    @staticmethod
    def normalize_command(text: str) -> str:
        """
        Привести текст к нижнему регистру,
        убрать лишние пробелы и знаки препинания.
        """
        text = text.lower().strip()
        text = re.sub(r"[^\w\s]", " ", text)   # пунктуация → пробел
        text = re.sub(r"\s+", " ", text)         # многократные пробелы → один
        return text

    # ─── Регистрация встроенных команд ──────────────────────────────────

    def _register_defaults(self) -> None:
        """Зарегистрировать стандартные системные команды."""

        # ── Блокнот ─────────────────────────────────────────────────────
        self.register_command(
            ["блокнот", "notepad", "текстовый редактор", "textedit"],
            tool_name="open_app",
            args={"app": "notepad"},
        )
        # ── Браузер ──────────────────────────────────────────────────────
        self.register_command(
            ["браузер", "browser", "хром", "chrome", "интернет", "firefox"],
            tool_name="open_app",
            args={"app": "browser"},
        )
        # ── Калькулятор ──────────────────────────────────────────────────
        self.register_command(
            ["калькулятор", "calculator", "считалка"],
            tool_name="open_app",
            args={"app": "calculator"},
        )
        # ── Выключение ───────────────────────────────────────────────────
        self.register_command(
            ["выключи", "выключить", "shutdown", "завершить работу", "выключи компьютер"],
            tool_name="shutdown_pc",
        )
        # ── Перезагрузка ─────────────────────────────────────────────────
        self.register_command(
            ["перезагрузи", "перезагрузка", "restart", "reboot", "перезапусти"],
            tool_name="restart_pc",
        )
        # ── Блокировка ───────────────────────────────────────────────────
        self.register_command(
            ["заблокируй", "блокировка", "lock", "заблокировать экран", "lock screen"],
            tool_name="lock_pc",
        )
