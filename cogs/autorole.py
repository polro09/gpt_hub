import os
import json
import asyncio
import logging
import pathlib
from typing import Optional, Dict, Any

import discord
from discord.ext import commands
from discord import app_commands

log = logging.getLogger("SDT-BOT.autorole")


def env_bool(key: str, default: bool = False) -> bool:
    v = os.getenv(key, str(int(default))).strip().lower()
    return v in ("1", "true", "yes", "y", "on")


BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = DATA_DIR / "autorole_config.json"


class AutoRoleConfigStore:
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
                    guilds[gid] = {"role_id": 0, "enabled": False}
                    changed = True
                    continue

                role_id = g.get("role_id", 0)
                enabled = g.get("enabled", False)

                try:
                    role_id = int(role_id or 0)
                except Exception:
                    role_id = 0

                enabled = bool(enabled)
                norm = {"role_id": role_id, "enabled": enabled}
                if g != norm:
                    guilds[gid] = norm
                    changed = True

            if changed:
                await self._save_unlocked()

    async def _save_unlocked(self):
        text = json.dumps(self.data, ensure_ascii=False, indent=2)
        await self._write_text(text)

    async def ensure_env_default(self, guild_id: int, role_id: int):
        if guild_id <= 0 or role_id <= 0:
            return

        async with self.lock:
            guilds = self.data.setdefault("guilds", {})
            key = str(guild_id)
            if key not in guilds:
                guilds[key] = {
                    "role_id": int(role_id),
                    "enabled": True,
                }
                await self._save_unlocked()

    async def set_guild(self, guild_id: int, role_id: int, enabled: bool = True):
        async with self.lock:
            self.data.setdefault("guilds", {})[str(guild_id)] = {
                "role_id": int(role_id),
                "enabled": bool(enabled),
            }
            await self._save_unlocked()

    async def remove_guild(self, guild_id: int):
        async with self.lock:
            self.data.setdefault("guilds", {}).pop(str(guild_id), None)
            await self._save_unlocked()

    async def get_guild(self, guild_id: int) -> Dict[str, Any]:
        async with self.lock:
            g = self.data.setdefault("guilds", {}).get(str(guild_id), {})
            if not isinstance(g, dict):
                return {"role_id": 0, "enabled": False}

            try:
                role_id = int(g.get("role_id", 0) or 0)
            except Exception:
                role_id = 0

            return {
                "role_id": role_id,
                "enabled": bool(g.get("enabled", False)),
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
                    role_id = int(g.get("role_id", 0) or 0)
                except Exception:
                    role_id = 0

                out[gid_int] = {
                    "role_id": role_id,
                    "enabled": bool(g.get("enabled", False)),
                }
            return out

    async def set_enabled(self, guild_id: int, enabled: bool):
        async with self.lock:
            guilds = self.data.setdefault("guilds", {})
            key = str(guild_id)
            g = guilds.setdefault(key, {"role_id": 0, "enabled": False})
            g["enabled"] = bool(enabled)
            await self._save_unlocked()


class AutoRole(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = AutoRoleConfigStore(CONFIG_PATH)

        self.legacy_auto_role_id: int = int(os.getenv("AUTO_ROLE_ID", "0") or "0")
        self.legacy_target_guild_id: int = int(os.getenv("AUTO_ROLE_GUILD_ID", "0") or "0")

        self.on_startup_scan: bool = env_bool("AUTO_ROLE_ON_STARTUP", False)
        self.include_bots: bool = env_bool("AUTO_ROLE_INCLUDE_BOTS", False)
        self._startup_scanned: set[int] = set()

        self.autorole_group = app_commands.Group(
            name="autorole",
            description="서버별 자동 역할 설정",
        )
        self._register_app_commands()

    async def cog_load(self):
        await self.store.load()

        if self.legacy_auto_role_id > 0 and self.legacy_target_guild_id > 0:
            await self.store.ensure_env_default(
                self.legacy_target_guild_id,
                self.legacy_auto_role_id,
            )

        log.info(
            "🎭 AutoRole loaded | startup_scan=%s include_bots=%s legacy_env_role=%s legacy_env_guild=%s",
            self.on_startup_scan,
            self.include_bots,
            self.legacy_auto_role_id,
            self.legacy_target_guild_id,
        )

    def _admin_only(self, interaction: discord.Interaction) -> bool:
        perms = interaction.user.guild_permissions  # type: ignore
        return bool(perms.administrator or perms.manage_guild)

    async def get_config_for_guild(self, guild: discord.Guild) -> Dict[str, Any]:
        return await self.store.get_guild(guild.id)

    async def is_enabled_for_guild(self, guild: discord.Guild) -> bool:
        cfg = await self.get_config_for_guild(guild)
        return bool(cfg.get("enabled")) and int(cfg.get("role_id", 0)) > 0

    async def get_role_for_guild(self, guild: discord.Guild) -> Optional[discord.Role]:
        cfg = await self.get_config_for_guild(guild)
        role_id = int(cfg.get("role_id", 0) or 0)
        if role_id <= 0:
            return None
        return guild.get_role(role_id)

    @staticmethod
    def is_roleless(member: discord.Member) -> bool:
        return len(member.roles) <= 1

    async def assign_role(self, member: discord.Member, reason: str) -> bool:
        if member.bot and not self.include_bots:
            return False

        enabled = await self.is_enabled_for_guild(member.guild)
        if not enabled:
            return False

        role = await self.get_role_for_guild(member.guild)
        if role is None:
            cfg = await self.get_config_for_guild(member.guild)
            log.warning(
                "🎭 역할을 찾지 못했습니다. guild=%s configured_role_id=%s",
                member.guild.name,
                cfg.get("role_id", 0),
            )
            return False

        if role in member.roles:
            return False

        try:
            await member.add_roles(role, reason=reason)
            log.info("🎭 역할 부여 성공: %s -> @%s (%s)", member, role.name, reason)
            return True
        except discord.Forbidden:
            log.error(
                "🎭 역할 부여 실패(Forbidden): 봇 권한/역할 계층 확인 필요. 봇의 최상위 역할이 '%s' 보다 위에 있어야 합니다.",
                role.name,
            )
        except discord.HTTPException as e:
            log.error("🎭 역할 부여 실패(HTTPException): %s", e)

        return False

    async def scan_guild_roleless(self, guild: discord.Guild, reason: str) -> tuple[int, int]:
        role = await self.get_role_for_guild(guild)
        if role is None:
            raise RuntimeError("Configured role not found in this guild.")

        checked = 0
        assigned = 0

        async for m in guild.fetch_members(limit=None):
            checked += 1

            if self.is_roleless(m):
                if await self.assign_role(m, reason=reason):
                    assigned += 1

            if checked % 50 == 0:
                await asyncio.sleep(1.0)

        return checked, assigned

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self.assign_role(member, reason="AutoRole: member joined")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        enabled = await self.is_enabled_for_guild(after.guild)
        if not enabled:
            return

        if self.is_roleless(after) and not self.is_roleless(before):
            await asyncio.sleep(0.5)
            await self.assign_role(after, reason="AutoRole: roleless detected")

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.on_startup_scan:
            return

        cfgs = await self.store.list_guilds()

        for guild in self.bot.guilds:
            cfg = cfgs.get(guild.id)
            if not cfg or not cfg.get("enabled") or guild.id in self._startup_scanned:
                continue

            self._startup_scanned.add(guild.id)

            role = await self.get_role_for_guild(guild)
            if role is None:
                log.warning("🔄 시작 스캔 스킵: %s | 설정 역할을 찾지 못함", guild.name)
                continue

            log.info("🔄 시작 스캔 시작: %s (members intent 필요)", guild.name)

            try:
                checked, assigned = await self.scan_guild_roleless(
                    guild,
                    reason="AutoRole: startup scan",
                )
                log.info("🔄 시작 스캔 완료: %s | 체크 %d명 / 부여 %d명", guild.name, checked, assigned)
            except discord.Forbidden:
                log.error("🔄 멤버 fetch 실패(Forbidden): Members Intent/권한 확인 필요 | guild=%s", guild.name)
            except discord.HTTPException as e:
                log.error("🔄 멤버 fetch 실패(HTTPException): %s | guild=%s", e, guild.name)
            except Exception as e:
                log.error("🔄 시작 스캔 실패: %s | %s", guild.name, e)

    def _register_app_commands(self):
        @self.autorole_group.command(name="set", description="현재 서버의 자동 지급 역할을 설정합니다.")
        @app_commands.describe(role="자동으로 지급할 역할")
        async def _set(interaction: discord.Interaction, role: discord.Role):
            await interaction.response.defer(ephemeral=True)

            if interaction.guild is None:
                return await interaction.followup.send("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)
            if not self._admin_only(interaction):
                return await interaction.followup.send("⛔ 관리자만 사용할 수 있습니다.", ephemeral=True)
            if role.is_default():
                return await interaction.followup.send("❌ @everyone 역할은 설정할 수 없습니다.", ephemeral=True)

            me = interaction.guild.me
            if me is None:
                return await interaction.followup.send("❌ 봇 멤버 정보를 확인할 수 없습니다.", ephemeral=True)
            if role >= me.top_role:
                return await interaction.followup.send(
                    "❌ 그 역할은 봇의 최상위 역할보다 높거나 같습니다. 봇 역할을 더 위로 올려주세요.",
                    ephemeral=True,
                )

            await self.store.set_guild(interaction.guild.id, role.id, enabled=True)

            e = discord.Embed(
                title="🎭 AutoRole 설정 완료",
                description=(
                    f"서버: **{interaction.guild.name}**\n"
                    f"지급 역할: {role.mention}\n"
                    f"상태: ✅ Enabled"
                ),
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=e, ephemeral=True)

        @self.autorole_group.command(name="enable", description="현재 서버의 자동 역할 기능을 활성화합니다.")
        async def _enable(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            if interaction.guild is None:
                return await interaction.followup.send("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)
            if not self._admin_only(interaction):
                return await interaction.followup.send("⛔ 관리자만 사용할 수 있습니다.", ephemeral=True)

            cfg = await self.store.get_guild(interaction.guild.id)
            if int(cfg.get("role_id", 0)) <= 0:
                return await interaction.followup.send("❌ 먼저 `/autorole set` 으로 역할을 지정하세요.", ephemeral=True)

            await self.store.set_enabled(interaction.guild.id, True)
            await interaction.followup.send("✅ 현재 서버의 AutoRole을 활성화했습니다.", ephemeral=True)

        @self.autorole_group.command(name="disable", description="현재 서버의 자동 역할 기능을 비활성화합니다.")
        async def _disable(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            if interaction.guild is None:
                return await interaction.followup.send("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)
            if not self._admin_only(interaction):
                return await interaction.followup.send("⛔ 관리자만 사용할 수 있습니다.", ephemeral=True)

            await self.store.set_enabled(interaction.guild.id, False)
            await interaction.followup.send("🛑 현재 서버의 AutoRole을 비활성화했습니다.", ephemeral=True)

        @self.autorole_group.command(name="clear", description="현재 서버의 자동 역할 설정을 제거합니다.")
        async def _clear(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            if interaction.guild is None:
                return await interaction.followup.send("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)
            if not self._admin_only(interaction):
                return await interaction.followup.send("⛔ 관리자만 사용할 수 있습니다.", ephemeral=True)

            await self.store.remove_guild(interaction.guild.id)
            await interaction.followup.send("🧹 현재 서버의 AutoRole 설정을 제거했습니다.", ephemeral=True)

        @self.autorole_group.command(name="status", description="현재 서버의 AutoRole 설정 상태를 확인합니다.")
        async def _status(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            if interaction.guild is None:
                return await interaction.followup.send("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)

            cfg = await self.store.get_guild(interaction.guild.id)
            role_id = int(cfg.get("role_id", 0) or 0)
            enabled = bool(cfg.get("enabled", False))
            role = interaction.guild.get_role(role_id) if role_id > 0 else None

            e = discord.Embed(
                title="📋 AutoRole 상태",
                color=discord.Color.green() if enabled else discord.Color.orange(),
            )
            e.add_field(name="서버", value=interaction.guild.name, inline=False)
            e.add_field(name="활성화", value="✅ Enabled" if enabled else "🛑 Disabled", inline=True)
            e.add_field(name="역할", value=(role.mention if role else f"없음 / role_id={role_id or 0}"), inline=True)
            e.add_field(name="봇 포함", value="✅" if self.include_bots else "❌", inline=True)
            e.add_field(name="시작 스캔", value="✅" if self.on_startup_scan else "❌", inline=True)
            await interaction.followup.send(embed=e, ephemeral=True)

        @self.autorole_group.command(name="scan", description="현재 서버의 무역할 멤버를 스캔하고 역할을 부여합니다.")
        async def _scan(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            if interaction.guild is None:
                return await interaction.followup.send("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)
            if not self._admin_only(interaction):
                return await interaction.followup.send("⛔ 관리자만 사용할 수 있습니다.", ephemeral=True)

            enabled = await self.is_enabled_for_guild(interaction.guild)
            if not enabled:
                return await interaction.followup.send(
                    "❌ 현재 서버에 활성화된 AutoRole 설정이 없습니다. `/autorole set` 후 사용하세요.",
                    ephemeral=True,
                )

            progress = await interaction.followup.send("🔎 무역할 멤버 스캔 시작…", ephemeral=True, wait=True)

            try:
                checked, assigned = await self.scan_guild_roleless(
                    interaction.guild,
                    reason=f"AutoRole: manual scan by {interaction.user}",
                )
                if hasattr(self.bot, "safe_editor"):
                    await self.bot.safe_editor.schedule_edit(
                        progress,
                        content=f"✅ 완료! 체크 **{checked}명**, 역할 부여 **{assigned}명**",
                    )
                else:
                    await progress.edit(content=f"✅ 완료! 체크 **{checked}명**, 역할 부여 **{assigned}명**")
            except discord.Forbidden:
                content = "❌ 실패: 멤버 목록 접근 권한(또는 Members Intent)을 확인하세요."
                if hasattr(self.bot, "safe_editor"):
                    await self.bot.safe_editor.schedule_edit(progress, content=content)
                else:
                    await progress.edit(content=content)
            except discord.HTTPException as e:
                content = f"❌ 실패: Discord API 오류: {e}"
                if hasattr(self.bot, "safe_editor"):
                    await self.bot.safe_editor.schedule_edit(progress, content=content)
                else:
                    await progress.edit(content=content)
            except Exception as e:
                content = f"❌ 실패: {e}"
                if hasattr(self.bot, "safe_editor"):
                    await self.bot.safe_editor.schedule_edit(progress, content=content)
                else:
                    await progress.edit(content=content)

    @commands.command(name="무역할부여", aliases=["autorole_scan"])
    @commands.has_permissions(administrator=True)
    async def cmd_assign_roleless(self, ctx: commands.Context):
        if ctx.guild is None:
            return

        enabled = await self.is_enabled_for_guild(ctx.guild)
        if not enabled:
            await ctx.reply("❌ 이 서버에는 활성화된 AutoRole 설정이 없습니다. `/autorole set` 으로 먼저 설정하세요.")
            return

        msg = await ctx.reply("🔎 무역할 멤버 스캔 시작…")

        try:
            checked, assigned = await self.scan_guild_roleless(
                ctx.guild,
                reason=f"AutoRole: manual scan by {ctx.author}",
            )
            if hasattr(self.bot, "safe_editor"):
                await self.bot.safe_editor.schedule_edit(
                    msg,
                    content=f"✅ 완료! 체크 **{checked}명**, 역할 부여 **{assigned}명**",
                )
            else:
                await msg.edit(content=f"✅ 완료! 체크 **{checked}명**, 역할 부여 **{assigned}명**")
        except discord.Forbidden:
            content = "❌ 실패: 멤버 목록 접근 권한(또는 Members Intent)을 확인하세요."
            if hasattr(self.bot, "safe_editor"):
                await self.bot.safe_editor.schedule_edit(msg, content=content)
            else:
                await msg.edit(content=content)
        except discord.HTTPException as e:
            content = f"❌ 실패: Discord API 오류: {e}"
            if hasattr(self.bot, "safe_editor"):
                await self.bot.safe_editor.schedule_edit(msg, content=content)
            else:
                await msg.edit(content=content)
        except Exception as e:
            content = f"❌ 실패: {e}"
            if hasattr(self.bot, "safe_editor"):
                await self.bot.safe_editor.schedule_edit(msg, content=content)
            else:
                await msg.edit(content=content)


async def setup(bot: commands.Bot):
    cog = AutoRole(bot)
    await bot.add_cog(cog)

    try:
        bot.tree.add_command(cog.autorole_group)
    except app_commands.CommandAlreadyRegistered:
        pass
