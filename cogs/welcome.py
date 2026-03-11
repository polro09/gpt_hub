import os
import json
import asyncio
import logging
import pathlib
from datetime import datetime, timezone
from typing import Optional, Dict, Any

import discord
from discord.ext import commands
from discord import app_commands

log = logging.getLogger("SDT-BOT")

WELCOME_COLOR = 0x57F287
GOODBYE_COLOR = 0xED4245
BOT_DISPLAY_NAME = "SDT Bot"

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = DATA_DIR / "welcome_config.json"

DEFAULT_WELCOME_IMAGE_URL = "https://i.imgur.com/C5pyeCn.gif"


class WelcomeConfigStore:
    """
    구조:
    {
      "guilds": {
        "928983859222159390": {
          "channel_id": 1456986512766800068,
          "image_url": "https://i.imgur.com/C5pyeCn.gif",
          "enabled": true
        }
      }
    }
    """

    def __init__(self, path: pathlib.Path):
        self.path = path
        self.lock = asyncio.Lock()
        self.data: Dict[str, Any] = {"guilds": {}}

    async def _read_text(self) -> str:
        def _r():
            return self.path.read_text(encoding="utf-8")
        return await asyncio.to_thread(_r)

    async def _write_text(self, text: str):
        def _w():
            self.path.write_text(text, encoding="utf-8")
        await asyncio.to_thread(_w)

    async def load(self):
        async with self.lock:
            if self.path.exists():
                try:
                    raw = await self._read_text()
                    self.data = json.loads(raw) if raw.strip() else {"guilds": {}}
                except Exception:
                    self.data = {"guilds": {}}
            else:
                self.data = {"guilds": {}}
                await self._save_unlocked()

            changed = False
            guilds = self.data.setdefault("guilds", {})
            if not isinstance(guilds, dict):
                self.data["guilds"] = {}
                guilds = self.data["guilds"]
                changed = True

            for gid, g in list(guilds.items()):
                if not isinstance(g, dict):
                    guilds[gid] = {
                        "channel_id": 0,
                        "image_url": DEFAULT_WELCOME_IMAGE_URL,
                        "enabled": False,
                    }
                    changed = True
                    continue

                try:
                    channel_id = int(g.get("channel_id", 0) or 0)
                except Exception:
                    channel_id = 0

                image_url = str(g.get("image_url", DEFAULT_WELCOME_IMAGE_URL) or DEFAULT_WELCOME_IMAGE_URL).strip()
                enabled = bool(g.get("enabled", False))

                norm = {
                    "channel_id": channel_id,
                    "image_url": image_url,
                    "enabled": enabled,
                }
                if g != norm:
                    guilds[gid] = norm
                    changed = True

            if changed:
                await self._save_unlocked()

    async def _save_unlocked(self):
        text = json.dumps(self.data, ensure_ascii=False, indent=2)
        await self._write_text(text)

    async def ensure_env_default(self, guild_id: int, channel_id: int):
        if guild_id <= 0 or channel_id <= 0:
            return

        async with self.lock:
            guilds = self.data.setdefault("guilds", {})
            key = str(guild_id)
            if key not in guilds:
                guilds[key] = {
                    "channel_id": int(channel_id),
                    "image_url": DEFAULT_WELCOME_IMAGE_URL,
                    "enabled": True,
                }
                await self._save_unlocked()

    async def set_guild(
        self,
        guild_id: int,
        *,
        channel_id: int,
        image_url: Optional[str] = None,
        enabled: bool = True,
    ):
        async with self.lock:
            guilds = self.data.setdefault("guilds", {})
            prev = guilds.get(str(guild_id), {})
            if not isinstance(prev, dict):
                prev = {}

            guilds[str(guild_id)] = {
                "channel_id": int(channel_id),
                "image_url": (image_url or prev.get("image_url") or DEFAULT_WELCOME_IMAGE_URL).strip(),
                "enabled": bool(enabled),
            }
            await self._save_unlocked()

    async def set_image_url(self, guild_id: int, image_url: str):
        async with self.lock:
            guilds = self.data.setdefault("guilds", {})
            prev = guilds.setdefault(
                str(guild_id),
                {
                    "channel_id": 0,
                    "image_url": DEFAULT_WELCOME_IMAGE_URL,
                    "enabled": False,
                },
            )
            prev["image_url"] = image_url.strip() if image_url.strip() else DEFAULT_WELCOME_IMAGE_URL
            await self._save_unlocked()

    async def set_enabled(self, guild_id: int, enabled: bool):
        async with self.lock:
            guilds = self.data.setdefault("guilds", {})
            prev = guilds.setdefault(
                str(guild_id),
                {
                    "channel_id": 0,
                    "image_url": DEFAULT_WELCOME_IMAGE_URL,
                    "enabled": False,
                },
            )
            prev["enabled"] = bool(enabled)
            await self._save_unlocked()

    async def remove_guild(self, guild_id: int):
        async with self.lock:
            self.data.setdefault("guilds", {}).pop(str(guild_id), None)
            await self._save_unlocked()

    async def get_guild(self, guild_id: int) -> Dict[str, Any]:
        async with self.lock:
            g = self.data.setdefault("guilds", {}).get(str(guild_id), {})
            if not isinstance(g, dict):
                return {
                    "channel_id": 0,
                    "image_url": DEFAULT_WELCOME_IMAGE_URL,
                    "enabled": False,
                }

            try:
                channel_id = int(g.get("channel_id", 0) or 0)
            except Exception:
                channel_id = 0

            image_url = str(g.get("image_url", DEFAULT_WELCOME_IMAGE_URL) or DEFAULT_WELCOME_IMAGE_URL).strip()
            enabled = bool(g.get("enabled", False))

            return {
                "channel_id": channel_id,
                "image_url": image_url,
                "enabled": enabled,
            }

    async def list_guilds(self) -> Dict[int, Dict[str, Any]]:
        async with self.lock:
            out: Dict[int, Dict[str, Any]] = {}
            for gid, g in self.data.setdefault("guilds", {}).items():
                try:
                    gid_int = int(gid)
                except Exception:
                    continue

                try:
                    channel_id = int(g.get("channel_id", 0) or 0)
                except Exception:
                    channel_id = 0

                out[gid_int] = {
                    "channel_id": channel_id,
                    "image_url": str(g.get("image_url", DEFAULT_WELCOME_IMAGE_URL) or DEFAULT_WELCOME_IMAGE_URL).strip(),
                    "enabled": bool(g.get("enabled", False)),
                }
            return out


class WelcomeModule(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = WelcomeConfigStore(CONFIG_PATH)

        # 기존 env는 초기 기본값 마이그레이션용
        self.legacy_welcome_guild_id = self._env_int("WELCOME_GUILD_ID", 0)
        self.legacy_welcome_channel_id = self._env_int("WELCOME_CHANNEL_ID", 0)

        self.welcome_group = app_commands.Group(
            name="welcome",
            description="서버별 웰컴/굿바이 설정",
        )
        self._register_app_commands()

    async def cog_load(self):
        await self.store.load()

        if self.legacy_welcome_guild_id > 0 and self.legacy_welcome_channel_id > 0:
            await self.store.ensure_env_default(
                self.legacy_welcome_guild_id,
                self.legacy_welcome_channel_id,
            )

        log.info(
            "[welcome] loaded | legacy_env_guild=%s legacy_env_channel=%s",
            self.legacy_welcome_guild_id,
            self.legacy_welcome_channel_id,
        )

    def _env_int(self, key: str, default: int = 0) -> int:
        try:
            return int(os.getenv(key, str(default)) or str(default))
        except Exception:
            return default

    def _admin_only(self, interaction: discord.Interaction) -> bool:
        perms = interaction.user.guild_permissions  # type: ignore
        return bool(perms.administrator or perms.manage_guild)

    async def _get_config(self, guild: discord.Guild) -> Dict[str, Any]:
        return await self.store.get_guild(guild.id)

    async def _is_enabled(self, guild: discord.Guild) -> bool:
        cfg = await self._get_config(guild)
        return bool(cfg.get("enabled")) and int(cfg.get("channel_id", 0)) > 0

    async def _get_channel(self, guild: discord.Guild) -> Optional[discord.abc.Messageable]:
        cfg = await self._get_config(guild)
        channel_id = int(cfg.get("channel_id", 0) or 0)

        log.info(f"[welcome] configured channel_id={channel_id} guild={guild.name}({guild.id})")

        if not channel_id:
            log.warning("[welcome] No welcome channel configured.")
            return None

        ch = guild.get_channel(channel_id)
        if ch is not None:
            return ch

        try:
            return await guild.fetch_channel(channel_id)
        except discord.Forbidden:
            log.exception("[welcome] Missing permission to fetch channel.")
            return None
        except discord.NotFound:
            log.exception("[welcome] Configured channel not found.")
            return None
        except discord.HTTPException:
            log.exception("[welcome] Failed to fetch channel (HTTPException).")
            return None

    async def _get_image_url(self, guild: discord.Guild) -> str:
        cfg = await self._get_config(guild)
        image_url = str(cfg.get("image_url", DEFAULT_WELCOME_IMAGE_URL) or DEFAULT_WELCOME_IMAGE_URL).strip()
        return image_url or DEFAULT_WELCOME_IMAGE_URL

    def _set_author(self, embed: discord.Embed):
        icon_url = self.bot.user.display_avatar.url if self.bot.user else None
        embed.set_author(name=BOT_DISPLAY_NAME, icon_url=icon_url)

    def _thumb(self, member: discord.Member) -> str:
        return member.display_avatar.replace(size=128).url

    def _date_with_dow(self, dt: Optional[datetime]) -> str:
        if not dt:
            return "Unknown"
        return dt.strftime("%Y. %m. %d. (%a)")

    def _format_duration(self, joined_at: Optional[datetime]) -> str:
        if not joined_at:
            return "Unknown"

        now = datetime.now(timezone.utc)
        total = int((now - joined_at).total_seconds())
        if total < 0:
            total = 0

        days = total // 86400
        hours = (total % 86400) // 3600
        minutes = (total % 3600) // 60

        parts = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        parts.append(f"{minutes}m")
        return " ".join(parts)

    def _box(self, text: str, lang: str = "ini") -> str:
        return f"```{lang}\n{text}\n```"

    def _welcome_embed(self, member: discord.Member) -> discord.Embed:
        embed = discord.Embed(
            title="👋 Welcome!",
            description=f"{member.mention} joined the server!",
            color=WELCOME_COLOR,
            timestamp=datetime.utcnow(),
        )
        self._set_author(embed)
        embed.set_thumbnail(url=self._thumb(member))

        info = "\n".join(
            [
                f"Username: {member.name}",
                f"User ID: {member.id}",
                f"Account Created: {self._date_with_dow(member.created_at)}",
                f"Server Joined: {self._date_with_dow(member.joined_at)}",
            ]
        )
        embed.add_field(name="👤 User Info", value=self._box(info, "ini"), inline=False)

        msg = "\n".join(
            [
                f"USER = {member.mention}",
                "STATUS = JOINED",
            ]
        )
        embed.add_field(name="🧾 Log", value=self._box(msg, "ini"), inline=False)
        embed.set_footer(text=f"{member.guild.name} • SDT Bot")
        return embed

    def _goodbye_embed(self, member: discord.Member) -> discord.Embed:
        stay = self._format_duration(member.joined_at)

        embed = discord.Embed(
            title="👋 Goodbye!",
            description=f"{member.mention} left the server.",
            color=GOODBYE_COLOR,
            timestamp=datetime.utcnow(),
        )
        self._set_author(embed)
        embed.set_thumbnail(url=self._thumb(member))

        info = "\n".join(
            [
                f"Username: {member.name}",
                f"User ID: {member.id}",
                f"Server Joined: {self._date_with_dow(member.joined_at)}",
                f"Time in Server: {stay}",
            ]
        )
        embed.add_field(name="👤 User Info", value=self._box(info, "ini"), inline=False)

        msg = "\n".join(
            [
                f"USER = {member.mention}",
                "STATUS = LEFT",
            ]
        )
        embed.add_field(name="🧾 Log", value=self._box(msg, "ini"), inline=False)
        embed.set_footer(text=f"{member.guild.name} • SDT Bot")
        return embed

    async def _send_with_image(self, guild: discord.Guild, channel: discord.abc.Messageable, embed: discord.Embed):
        try:
            embed.set_image(url=await self._get_image_url(guild))
            await channel.send(embed=embed)
        except discord.Forbidden:
            log.exception("[welcome] Missing permission to send.")
        except discord.HTTPException:
            log.exception("[welcome] Failed to send welcome embed.")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not await self._is_enabled(member.guild):
            return

        log.info(f"[welcome] on_member_join: {member} in {member.guild.name}({member.guild.id})")
        channel = await self._get_channel(member.guild)
        if channel:
            await self._send_with_image(member.guild, channel, self._welcome_embed(member))
        else:
            log.error("[welcome] Failed to locate welcome channel. Not sent.")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if not await self._is_enabled(member.guild):
            return

        log.info(f"[welcome] on_member_remove: {member} in {member.guild.name}({member.guild.id})")
        channel = await self._get_channel(member.guild)
        if channel:
            await self._send_with_image(member.guild, channel, self._goodbye_embed(member))
        else:
            log.error("[welcome] Failed to locate welcome channel. Not sent.")

    def _register_app_commands(self):
        @self.welcome_group.command(name="set", description="현재 서버의 웰컴 채널을 설정합니다.")
        @app_commands.describe(channel="웰컴/굿바이 메시지를 보낼 채널")
        async def _set(interaction: discord.Interaction, channel: discord.TextChannel):
            await interaction.response.defer(ephemeral=True)

            if interaction.guild is None:
                return await interaction.followup.send("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)
            if not self._admin_only(interaction):
                return await interaction.followup.send("⛔ 관리자만 사용할 수 있습니다.", ephemeral=True)

            me = interaction.guild.me
            if me is None:
                return await interaction.followup.send("❌ 봇 멤버 정보를 확인할 수 없습니다.", ephemeral=True)

            perms = channel.permissions_for(me)
            if not perms.send_messages or not perms.embed_links:
                return await interaction.followup.send(
                    "❌ 해당 채널에 봇의 `메시지 보내기` 및 `임베드 링크` 권한이 필요합니다.",
                    ephemeral=True,
                )

            await self.store.set_guild(
                interaction.guild.id,
                channel_id=channel.id,
                enabled=True,
            )

            e = discord.Embed(
                title="👋 Welcome 설정 완료",
                description=(
                    f"서버: **{interaction.guild.name}**\n"
                    f"채널: {channel.mention}\n"
                    f"상태: ✅ Enabled"
                ),
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=e, ephemeral=True)

        @self.welcome_group.command(name="image", description="현재 서버의 웰컴 임베드 이미지 URL을 설정합니다.")
        @app_commands.describe(image_url="임베드에 표시할 이미지 URL")
        async def _image(interaction: discord.Interaction, image_url: str):
            await interaction.response.defer(ephemeral=True)

            if interaction.guild is None:
                return await interaction.followup.send("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)
            if not self._admin_only(interaction):
                return await interaction.followup.send("⛔ 관리자만 사용할 수 있습니다.", ephemeral=True)

            image_url = image_url.strip()
            if not (image_url.startswith("http://") or image_url.startswith("https://")):
                return await interaction.followup.send("❌ 올바른 이미지 URL(http/https)만 사용할 수 있습니다.", ephemeral=True)

            await self.store.set_image_url(interaction.guild.id, image_url)

            e = discord.Embed(
                title="🖼️ Welcome 이미지 설정 완료",
                description=f"서버: **{interaction.guild.name}**\n이미지 URL:\n{image_url}",
                color=discord.Color.green(),
            )
            e.set_image(url=image_url)
            await interaction.followup.send(embed=e, ephemeral=True)

        @self.welcome_group.command(name="enable", description="현재 서버의 웰컴 기능을 활성화합니다.")
        async def _enable(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)

            if interaction.guild is None:
                return await interaction.followup.send("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)
            if not self._admin_only(interaction):
                return await interaction.followup.send("⛔ 관리자만 사용할 수 있습니다.", ephemeral=True)

            cfg = await self.store.get_guild(interaction.guild.id)
            if int(cfg.get("channel_id", 0)) <= 0:
                return await interaction.followup.send(
                    "❌ 먼저 `/welcome set` 으로 채널을 지정하세요.",
                    ephemeral=True,
                )

            await self.store.set_enabled(interaction.guild.id, True)
            await interaction.followup.send("✅ 현재 서버의 Welcome 기능을 활성화했습니다.", ephemeral=True)

        @self.welcome_group.command(name="disable", description="현재 서버의 웰컴 기능을 비활성화합니다.")
        async def _disable(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)

            if interaction.guild is None:
                return await interaction.followup.send("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)
            if not self._admin_only(interaction):
                return await interaction.followup.send("⛔ 관리자만 사용할 수 있습니다.", ephemeral=True)

            await self.store.set_enabled(interaction.guild.id, False)
            await interaction.followup.send("🛑 현재 서버의 Welcome 기능을 비활성화했습니다.", ephemeral=True)

        @self.welcome_group.command(name="clear", description="현재 서버의 웰컴 설정을 제거합니다.")
        async def _clear(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)

            if interaction.guild is None:
                return await interaction.followup.send("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)
            if not self._admin_only(interaction):
                return await interaction.followup.send("⛔ 관리자만 사용할 수 있습니다.", ephemeral=True)

            await self.store.remove_guild(interaction.guild.id)
            await interaction.followup.send("🧹 현재 서버의 Welcome 설정을 제거했습니다.", ephemeral=True)

        @self.welcome_group.command(name="status", description="현재 서버의 Welcome 설정 상태를 확인합니다.")
        async def _status(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)

            if interaction.guild is None:
                return await interaction.followup.send("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)

            cfg = await self.store.get_guild(interaction.guild.id)
            channel_id = int(cfg.get("channel_id", 0) or 0)
            image_url = str(cfg.get("image_url", DEFAULT_WELCOME_IMAGE_URL) or DEFAULT_WELCOME_IMAGE_URL).strip()
            enabled = bool(cfg.get("enabled", False))
            channel = interaction.guild.get_channel(channel_id) if channel_id > 0 else None

            e = discord.Embed(
                title="📋 Welcome 상태",
                color=discord.Color.green() if enabled else discord.Color.orange(),
            )
            e.add_field(name="서버", value=interaction.guild.name, inline=False)
            e.add_field(name="활성화", value="✅ Enabled" if enabled else "🛑 Disabled", inline=True)
            e.add_field(name="채널", value=(channel.mention if channel else f"없음 / channel_id={channel_id or 0}"), inline=True)
            e.add_field(name="이미지 URL", value=image_url[:1024], inline=False)
            e.set_image(url=image_url)

            await interaction.followup.send(embed=e, ephemeral=True)


async def setup(bot: commands.Bot):
    cog = WelcomeModule(bot)
    await bot.add_cog(cog)

    try:
        bot.tree.add_command(cog.welcome_group)
    except app_commands.CommandAlreadyRegistered:
        pass

    log.info("[welcome] cogs.welcome loaded")