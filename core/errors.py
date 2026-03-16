import logging
import traceback

import discord
from discord.ext import commands
from discord import app_commands

log = logging.getLogger("SDT-BOT.errors")



def register_error_handlers(bot: commands.Bot) -> None:
    @bot.tree.error
    async def on_app_command_error(
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        cmd = getattr(interaction, "command", None)
        log.error(f"🧭 AppCommand ERROR: {cmd} | {type(error).__name__}: {error}")
        log.error(traceback.format_exc())
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ 명령어 실행 중 오류가 발생했습니다. 로그를 확인해주세요.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "❌ 명령어 실행 중 오류가 발생했습니다. 로그를 확인해주세요.",
                    ephemeral=True,
                )
        except Exception:
            pass

    @bot.event
    async def on_command_error(ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandNotFound):
            return

        log.error(
            f"⌨️ Prefix Command ERROR: {getattr(ctx.command, 'qualified_name', 'unknown')} | "
            f"{type(error).__name__}: {error}"
        )
        log.error(traceback.format_exc())

        try:
            await ctx.reply("❌ 명령어 실행 중 오류가 발생했습니다.", mention_author=False)
        except Exception:
            pass
