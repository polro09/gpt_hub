import asyncio
import logging
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands

log = logging.getLogger("SDT-BOT")


class ChannelCleanup(commands.Cog):
    """
    채널 메시지 정리 모듈
    - !채널정리 [#채널] : 14일 이내 메시지 일괄 삭제(빠름)
    - !채널정리올 [#채널] : 14일 지난 메시지까지 전부 삭제(느림/레이트리밋 주의)
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _is_manager(self, member: discord.Member) -> bool:
        return member.guild_permissions.manage_messages or member.guild_permissions.administrator

    async def _safe_edit(self, message: discord.Message, *, content: str):
        if hasattr(self.bot, "safe_editor"):
            await self.bot.safe_editor.schedule_edit(message, content=content)
        else:
            await message.edit(content=content)

    async def _purge_recent(self, channel: discord.TextChannel, *, reason: str) -> int:
        deleted = await channel.purge(
            limit=None,
            check=lambda m: True,
            reason=reason,
            bulk=True,
        )
        return len(deleted)

    async def _delete_all_including_old(self, channel: discord.TextChannel, *, reason: str) -> tuple[int, int]:
        recent_deleted = await self._purge_recent(channel, reason=reason)

        cutoff = datetime.now(timezone.utc) - timedelta(days=14)
        old_deleted = 0

        async for msg in channel.history(limit=None, oldest_first=False):
            created = msg.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)

            if created > cutoff:
                continue

            try:
                await msg.delete(reason=reason)
                old_deleted += 1
                await asyncio.sleep(0.35)
            except discord.Forbidden:
                log.exception("[cleanup] 메시지 삭제 권한 부족")
                break
            except discord.HTTPException:
                log.exception("[cleanup] 메시지 개별 삭제 중 HTTPException")
                await asyncio.sleep(1.5)

        return recent_deleted, old_deleted

    @commands.command(name="채널정리")
    @commands.guild_only()
    async def cleanup_channel(self, ctx: commands.Context, channel: discord.TextChannel | None = None):
        if not isinstance(ctx.author, discord.Member) or not self._is_manager(ctx.author):
            return await ctx.reply("❌ 이 명령어는 **메시지 관리 권한(Manage Messages)** 이 필요합니다.", mention_author=False)

        target = channel or ctx.channel
        if not isinstance(target, discord.TextChannel):
            return await ctx.reply("❌ 텍스트 채널에서만 사용할 수 있어요.", mention_author=False)

        perms = target.permissions_for(ctx.guild.me)
        if not (perms.manage_messages and perms.read_message_history):
            return await ctx.reply("❌ 봇에 **메시지 관리(Manage Messages)** 와 **메시지 기록 보기(Read Message History)** 권한이 필요합니다.", mention_author=False)

        notice = await ctx.reply(f"🧹 `{target.name}` 채널을 정리 중... (14일 이내 메시지)", mention_author=False)

        reason = f"Channel cleanup by {ctx.author} ({ctx.author.id})"
        try:
            deleted_count = await self._purge_recent(target, reason=reason)
            await self._safe_edit(
                notice,
                content=f"✅ 완료! `{target.name}`에서 **{deleted_count}개** 메시지를 삭제했어요. (14일 이내)",
            )
        except discord.HTTPException:
            log.exception("[cleanup] purge 실패")
            await self._safe_edit(notice, content="❌ 정리 중 오류가 발생했어요. (HTTPException)")

    @commands.command(name="채널정리올")
    @commands.guild_only()
    async def cleanup_channel_all(self, ctx: commands.Context, channel: discord.TextChannel | None = None):
        if not isinstance(ctx.author, discord.Member) or not self._is_manager(ctx.author):
            return await ctx.reply("❌ 이 명령어는 **메시지 관리 권한(Manage Messages)** 이 필요합니다.", mention_author=False)

        target = channel or ctx.channel
        if not isinstance(target, discord.TextChannel):
            return await ctx.reply("❌ 텍스트 채널에서만 사용할 수 있어요.", mention_author=False)

        perms = target.permissions_for(ctx.guild.me)
        if not (perms.manage_messages and perms.read_message_history):
            return await ctx.reply("❌ 봇에 **메시지 관리(Manage Messages)** 와 **메시지 기록 보기(Read Message History)** 권한이 필요합니다.", mention_author=False)

        notice = await ctx.reply(
            f"🧹 `{target.name}` 채널을 **완전 정리** 중... (14일 지난 메시지는 느릴 수 있어요)",
            mention_author=False
        )

        reason = f"Full channel cleanup by {ctx.author} ({ctx.author.id})"
        try:
            recent, old = await self._delete_all_including_old(target, reason=reason)
            await self._safe_edit(
                notice,
                content=f"✅ 완료! `{target.name}`에서 삭제: **최근 {recent}개 + 오래된 {old}개 = 총 {recent + old}개**",
            )
        except discord.HTTPException:
            log.exception("[cleanup] 전체 정리 실패")
            await self._safe_edit(notice, content="❌ 정리 중 오류가 발생했어요. (HTTPException)")


async def setup(bot: commands.Bot):
    await bot.add_cog(ChannelCleanup(bot))
    log.info("[cleanup] cogs.channel_cleanup loaded")
