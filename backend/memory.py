"""
backend/memory.py
═══════════════════════════════════════════════════════════════════════════════
Долговременная память GIDEON.

Хранит факты о пользователе (имя, предпочтения и т.д.) в JSON-файле,
который переживает перезапуски. Гидеон может запоминать и вспоминать.

Класс Memory:
  remember(key, value)  — запомнить факт
  recall(key)           — вспомнить факт по ключу
  forget(key)           — забыть факт
  all_facts()           — все факты (для контекста LLM)
  search(query)         — найти факты по подстроке

Файл памяти: gideon_memory.json рядом с проектом (или в %APPDATA%).
"""

import json
import logging
import os
import threading

logger = logging.getLogger("gideon.memory")


class Memory:
    """Простое key-value хранилище фактов на диске (JSON)."""

    def __init__(self, path: str = "") -> None:
        if not path:
            # Хранить в папке пользователя, чтобы переживать обновления проекта
            base = os.environ.get("APPDATA") or os.path.expanduser("~")
            folder = os.path.join(base, "Gideon")
            try:
                os.makedirs(folder, exist_ok=True)
                path = os.path.join(folder, "gideon_memory.json")
            except Exception:
                path = "gideon_memory.json"  # запасной вариант — рядом с проектом

        self.path = path
        self._lock = threading.Lock()
        self._data: dict = {}
        self._load()

    # ─── Загрузка / сохранение ─────────────────────────────────────────────

    def _load(self) -> None:
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                logger.info("Память загружена: %d фактов из %s",
                            len(self._data), self.path)
            else:
                self._data = {}
        except Exception as exc:
            logger.warning("Не удалось загрузить память: %s", exc)
            self._data = {}

    def _save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("Не удалось сохранить память: %s", exc)

    # ─── Публичный API ──────────────────────────────────────────────────────

    def remember(self, key: str, value: str) -> None:
        """Запомнить факт. Ключ нормализуется (нижний регистр, без пробелов по краям)."""
        key = (key or "").strip().lower()
        value = (value or "").strip()
        if not key or not value:
            return
        with self._lock:
            self._data[key] = value
            self._save()
        logger.info("Запомнено: %s = %r", key, value)

    def recall(self, key: str) -> str | None:
        """Вспомнить факт по ключу. None если нет."""
        key = (key or "").strip().lower()
        with self._lock:
            return self._data.get(key)

    def forget(self, key: str) -> bool:
        """Забыть факт. True если был удалён."""
        key = (key or "").strip().lower()
        with self._lock:
            if key in self._data:
                del self._data[key]
                self._save()
                logger.info("Забыто: %s", key)
                return True
        return False

    def all_facts(self) -> dict:
        """Все запомненные факты (копия)."""
        with self._lock:
            return dict(self._data)

    def search(self, query: str) -> dict:
        """Найти факты, где ключ или значение содержит подстроку."""
        q = (query or "").strip().lower()
        if not q:
            return {}
        with self._lock:
            return {
                k: v for k, v in self._data.items()
                if q in k.lower() or q in v.lower()
            }

    def context_string(self) -> str:
        """
        Сжатое строковое представление памяти — для вставки в системный
        промпт LLM, чтобы Гидеон «знал» факты при ответах.
        """
        facts = self.all_facts()
        if not facts:
            return ""
        lines = [f"- {k}: {v}" for k, v in facts.items()]
        return "Известные факты о пользователе:\n" + "\n".join(lines)
