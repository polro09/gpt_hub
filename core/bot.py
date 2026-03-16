import os
import pathlib
import logging
import traceback

import discord
from discord.ext import commands

from core.env import validate_env
from core.extensions import discover_extensions
from core.hooks import register_persistent_views
from core.errors import register_error_handlers
from utils.message_editor import SafeMessageEditor

log = logging.getLogger("SDT-BOT")
BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
COGS_DIR = BASE_DIR / "cogs"


class SDTBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.message_content = True

        super().__init__(
            command_prefix=commands.when_mentioned_or("!"),
            intents=intents,
            help_command=None,
        )

        self.base_dir = BASE_DIR
        self.cogs_dir = COGS_DIR
        self.startup_extensions: list[str] = []
        self.safe_editor = SafeMessageEditor(delay=2.0)

    async def setup_hook(self) -> None:
        log.info("🚀 setup_hook started")
        self.startup_extensions = discover_extensions(self.cogs_dir)
        await self.load_all_extensions()
        await register_persistent_views(self, self.startup_extensions)
        await self.sync_app_commands()
        log.info("✅ setup_hook completed")

    async def load_all_extensions(self) -> None:
        if not self.startup_extensions:
            log.warning("🧩 No extensions found to load.")
            return

        for ext in self.startup_extensions:
            try:
                await self.load_extension(ext)
                log.info(f"🧩 Extension loaded: {ext}")
            except commands.ExtensionAlreadyLoaded:
                log.warning(f"🧩 Extension already loaded: {ext}")
            except Exception:
                log.error(f"🧩 Extension load FAILED: {ext}\n{traceback.format_exc()}")

    async def sync_app_commands(self) -> None:
        try:
            synced = await self.tree.sync()
            log.info(f"🧭 Slash commands synced: {len(synced)}")
        except Exception:
            log.error(f"🧭 Slash command sync FAILED\n{traceback.format_exc()}")

    async def is_owner_or_admin(self, user: discord.abc.User) -> bool:
        app_info = await self.application_info()
        owner_ids = set()

        if app_info.owner:
            owner_ids.add(app_info.owner.id)
        if app_info.team:
            for member in app_info.team.members:
                owner_ids.add(member.id)

        return user.id in owner_ids



def create_bot() -> SDTBot:
    bot = SDTBot()
    register_error_handlers(bot)

    @bot.event
    async def on_ready() -> None:
        if bot.user is None:
            return

        log.info(f"🤖 Logged in: {bot.user} (ID: {bot.user.id})")
        log.info(f"🏠 Connected guilds: {len(bot.guilds)}")

        try:
            cog = bot.get_cog("ResourceRequestCog")
            if cog and hasattr(cog, "_reattach_log_views"):
                await cog._reattach_log_views()
                log.info("🧷 Resource log views reattached (on_ready)")
        except Exception:
            log.warning(f"🧷 Resource log views reattach skipped (on_ready)\n{traceback.format_exc()}")

    return bot



def run_bot(bot: SDTBot) -> None:
    validate_env(["DISCORD_TOKEN"])
    token = os.getenv("DISCORD_TOKEN", "").strip()

    try:
        bot.run(token, log_handler=None)
    except discord.LoginFailure:
        log.error("🔐 Login failed: DISCORD_TOKEN is invalid.")
    except KeyboardInterrupt:
        log.warning("🛑 Bot stopped by keyboard interrupt.")
    except Exception:
        log.error(f"🧨 bot.run crashed\n{traceback.format_exc()}")
