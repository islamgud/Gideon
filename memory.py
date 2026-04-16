"""
memory.py — простое JSON-хранилище для GIDEON
"""

import json
import os


MEMORY_FILE = "gideon_memory.json"


class Memory:
    def __init__(self, filepath: str = MEMORY_FILE):
        self.filepath = filepath
        self._data: dict = self._load_file()

    # ── внутреннее чтение с диска ─────────────────────────────
    def _load_file(self) -> dict:
        if not os.path.exists(self.filepath):
            return {}
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    # ── внутренняя запись на диск ─────────────────────────────
    def _save_file(self) -> None:
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    # ── публичный API ─────────────────────────────────────────
    def save(self, key: str, value) -> None:
        """Сохранить значение по ключу."""
        self._data[key] = value
        self._save_file()

    def load(self, key: str, default=None):
        """Загрузить значение по ключу. Если нет — вернуть default."""
        return self._data.get(key, default)

    def delete(self, key: str) -> None:
        """Удалить ключ, если существует."""
        if key in self._data:
            del self._data[key]
            self._save_file()

    def all(self) -> dict:
        """Вернуть всё содержимое памяти."""
        return dict(self._data)

    def clear(self) -> None:
        """Очистить всю память."""
        self._data = {}
        self._save_file()
