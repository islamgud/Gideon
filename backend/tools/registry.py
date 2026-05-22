"""
backend/tools/registry.py
Реестр инструментов: декоратор @tool регистрирует функцию,
execute() вызывает её по имени.
"""

import logging
from typing import Callable, Awaitable

logger = logging.getLogger("gideon.tools")

_registry: dict[str, Callable[[dict], Awaitable[dict]]] = {}


def tool(name: str):
    """Декоратор для регистрации инструмента."""
    def decorator(fn):
        _registry[name] = fn
        logger.info("Зарегистрирован tool: %s", name)
        return fn
    return decorator


async def execute(name: str, args: dict) -> dict:
    """Вызвать инструмент по имени."""
    fn = _registry.get(name)
    if not fn:
        return {"error": f"Неизвестный tool: {name}"}

    try:
        return await fn(args)
    except Exception as e:
        logger.exception("Ошибка в tool %s", name)
        return {"error": str(e)}


def list_tools() -> list[str]:
    """Список зарегистрированных инструментов (для дебага)."""
    return list(_registry.keys())