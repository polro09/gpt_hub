import asyncio
import logging
from typing import Optional

import discord

log = logging.getLogger("SDT-BOT.safeedit")


class SafeMessageEditor:
    def __init__(self, delay: float = 2.0):
        self.delay = delay
        self._pending: dict[int, dict] = {}
        self._tasks: dict[int, asyncio.Task] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    def _get_lock(self, message_id: int) -> asyncio.Lock:
        lock = self._locks.get(message_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[message_id] = lock
        return lock

    async def schedule_edit(
        self,
        message: discord.Message,
        *,
        content: Optional[str] = None,
        embed: Optional[discord.Embed] = None,
        view: Optional[discord.ui.View] = None,
        attachments=None,
        suppress: Optional[bool] = None,
        delete_after: Optional[float] = None,
    ):
        self._pending[message.id] = {
            "message": message,
            "content": content,
            "embed": embed,
            "view": view,
            "attachments": attachments,
            "suppress": suppress,
            "delete_after": delete_after,
        }

        old_task = self._tasks.get(message.id)
        if old_task and not old_task.done():
            old_task.cancel()

        self._tasks[message.id] = asyncio.create_task(self._flush_later(message.id))

    async def _flush_later(self, message_id: int):
        try:
            await asyncio.sleep(self.delay)
            data = self._pending.pop(message_id, None)
            if not data:
                return

            message: discord.Message = data["message"]

            async with self._get_lock(message_id):
                try:
                    await message.edit(
                        content=data["content"],
                        embed=data["embed"],
                        view=data["view"],
                        attachments=data["attachments"],
                        suppress=data["suppress"],
                        delete_after=data["delete_after"],
                    )
                except discord.NotFound:
                    log.warning(f"[schedule_edit] message not found: {message_id}")
                except discord.Forbidden:
                    log.warning(f"[schedule_edit] forbidden editing message: {message_id}")
                except discord.HTTPException as e:
                    log.warning(f"[schedule_edit] edit failed: {message_id} | {e}")
        except asyncio.CancelledError:
            pass
