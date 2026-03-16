import asyncio
import importlib
import logging
import traceback
from typing import Iterable

import discord

log = logging.getLogger("SDT-BOT.hooks")


async def maybe_call(module_name: str, func_name: str, *args, **kwargs) -> bool:
    try:
        module = importlib.import_module(module_name)
        func = getattr(module, func_name, None)
        if callable(func):
            result = func(*args, **kwargs)
            if asyncio.iscoroutine(result):
                await result
            return True
    except Exception:
        log.warning(f"Optional hook failed: {module_name}.{func_name}\n{traceback.format_exc()}")
    return False


async def register_persistent_views(bot: discord.Client, extensions: Iterable[str]) -> None:
    for ext in extensions:
        await maybe_call(ext, "register_persistent_views", bot)
