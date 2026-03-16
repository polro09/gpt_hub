import discord
from discord.ext import commands


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_check(self, ctx: commands.Context) -> bool:
        return await self.bot.is_owner_or_admin(ctx.author)

    @commands.command(name="ping")
    async def ping_command(self, ctx: commands.Context):
        latency_ms = round(self.bot.latency * 1000)
        await ctx.reply(f"🏓 Pong! `{latency_ms}ms`", mention_author=False)

    @commands.command(name="extensions")
    @commands.guild_only()
    async def extensions_command(self, ctx: commands.Context):
        loaded = sorted(self.bot.extensions.keys())
        if not loaded:
            await ctx.reply("불러와진 extension이 없습니다.", mention_author=False)
            return

        msg = "🧩 Loaded extensions:\n" + "\n".join(f"- `{name}`" for name in loaded)
        await ctx.reply(msg[:1900], mention_author=False)

    @commands.command(name="reload")
    @commands.guild_only()
    async def reload_extension_command(self, ctx: commands.Context, extension: str):
        ext = extension if extension.startswith("cogs.") else f"cogs.{extension}"
        try:
            await self.bot.reload_extension(ext)
            await ctx.reply(f"♻️ 리로드 완료: `{ext}`", mention_author=False)
        except commands.ExtensionNotLoaded:
            await self.bot.load_extension(ext)
            await ctx.reply(f"✅ 로드 완료: `{ext}`", mention_author=False)

    @commands.command(name="load")
    @commands.guild_only()
    async def load_extension_command(self, ctx: commands.Context, extension: str):
        ext = extension if extension.startswith("cogs.") else f"cogs.{extension}"
        await self.bot.load_extension(ext)
        await ctx.reply(f"✅ 로드 완료: `{ext}`", mention_author=False)

    @commands.command(name="unload")
    @commands.guild_only()
    async def unload_extension_command(self, ctx: commands.Context, extension: str):
        ext = extension if extension.startswith("cogs.") else f"cogs.{extension}"
        await self.bot.unload_extension(ext)
        await ctx.reply(f"🗑️ 언로드 완료: `{ext}`", mention_author=False)

    @commands.command(name="sync")
    @commands.guild_only()
    async def sync_command(self, ctx: commands.Context, mode: str = "global"):
        if mode.lower() == "guild":
            synced = await self.bot.tree.sync(guild=ctx.guild)
            await ctx.reply(f"🧭 현재 서버 기준 slash sync 완료: {len(synced)}개", mention_author=False)
        else:
            synced = await self.bot.tree.sync()
            await ctx.reply(f"🧭 글로벌 slash sync 완료: {len(synced)}개", mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
