import json
import pathlib
import discord
from discord.ext import commands
from datetime import datetime, timezone, timedelta
from typing import List, Optional

# ✅ 로그 채널(고정)
REQUEST_LOG_CHANNEL_ID = 1466787093471690783

# ✅ 요청 가능한 자원(고정)
RESOURCE_ITEMS = ["목재들보", "격벽", "캔버스", "청동", "판금"]

# ✅ !자원요청 임베드 이미지(GIF)
REQUEST_EMBED_IMAGE_URL = "https://i.imgur.com/Qa0AmZo.gif"

# ✅ Persistent Button custom_id (고정 필수)
PERSISTENT_REQUEST_BUTTON_ID = "resource_request_button"

# ✅ 저장(재시작 복구용)
BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_INDEX_PATH = DATA_DIR / "resource_request_log_index.json"

KST = timezone(timedelta(hours=9))


# =========================
# 저장 유틸
# =========================
def _load_log_index() -> dict:
    if not LOG_INDEX_PATH.exists():
        return {"log_message_ids": []}
    try:
        return json.loads(LOG_INDEX_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"log_message_ids": []}


def _save_log_index(data: dict) -> None:
    LOG_INDEX_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_admin(member: discord.Member) -> bool:
    p = member.guild_permissions
    return bool(p.administrator or p.manage_guild or p.manage_messages)


def _set_done_footer(embed: discord.Embed, by_user_id: int, done_at_kst: str) -> None:
    # footer는 기계 판독용 (취소/검증을 위해 처리자 저장)
    embed.set_footer(text=f"RR_STATUS=DONE|BY={by_user_id}|AT={done_at_kst}")


def _clear_done_footer(embed: discord.Embed) -> None:
    embed.set_footer(text="")


def _get_done_by_user_id(embed: discord.Embed) -> Optional[int]:
    ft = (embed.footer.text or "").strip() if embed.footer else ""
    if "RR_STATUS=DONE" not in ft or "BY=" not in ft:
        return None
    try:
        parts = ft.split("|")
        by_part = next(p for p in parts if p.startswith("BY="))
        return int(by_part.replace("BY=", "").strip())
    except Exception:
        return None


def _has_done(embed: discord.Embed) -> bool:
    # footer 우선
    ft = (embed.footer.text or "").strip() if embed.footer else ""
    if "RR_STATUS=DONE" in ft:
        return True
    # fallback: 필드 검사
    for f in (embed.fields or []):
        if f.name == "처리 상태" and "✅" in (f.value or ""):
            return True
    return False


def _update_status_field(embed: discord.Embed, value: str) -> None:
    """
    discord.py에는 discord.EmbedField가 없습니다.
    따라서 기존 fields를 (name, value, inline)로 안전하게 재구성합니다.
    """
    preserved = [(f.name, f.value, f.inline) for f in (embed.fields or [])]

    replaced = False
    new_list = []
    for name, val, inline in preserved:
        if name == "처리 상태":
            new_list.append(("처리 상태", value, False))
            replaced = True
        else:
            new_list.append((name, val, inline))

    if not replaced:
        new_list.append(("처리 상태", value, False))

    embed.clear_fields()
    for name, val, inline in new_list:
        embed.add_field(name=name, value=val, inline=inline)


# =========================
# 모달(수량+사유)
# =========================
class ResourceFormModal(discord.ui.Modal):
    def __init__(self, item_name: str):
        super().__init__(title="자원 요청 입력")
        self.item_name = item_name

        self.qty = discord.ui.TextInput(
            label=f"{item_name} 수량",
            placeholder="예) 10",
            required=True,
            max_length=6,
        )
        self.reason = discord.ui.TextInput(
            label="요청 사유",
            placeholder="예) 항구전 준비 / 선박 수리 / 길드 지원 요청 등",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=500,
        )

        self.add_item(self.qty)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("❌ 길드에서만 사용하실 수 있습니다.", ephemeral=True)

        raw = (self.qty.value or "").strip().replace(",", "")
        if not raw.isdigit():
            return await interaction.response.send_message("❌ 수량은 숫자만 입력해 주세요.", ephemeral=True)

        qty = int(raw)
        if qty <= 0:
            return await interaction.response.send_message("❌ 수량은 1 이상이어야 합니다.", ephemeral=True)
        if qty > 99999:
            return await interaction.response.send_message("❌ 수량이 너무 큽니다. 99999 이하로 입력해 주세요.", ephemeral=True)

        reason = (self.reason.value or "").strip()
        if not reason:
            return await interaction.response.send_message("❌ 요청 사유를 입력해 주세요.", ephemeral=True)

        # 로그 채널 찾기
        log_ch = guild.get_channel(REQUEST_LOG_CHANNEL_ID)
        if not log_ch:
            try:
                log_ch = await guild.fetch_channel(REQUEST_LOG_CHANNEL_ID)
            except Exception:
                log_ch = None

        if not isinstance(log_ch, (discord.TextChannel, discord.Thread)):
            return await interaction.response.send_message(
                f"❌ 로그 채널을 찾을 수 없습니다. (ID: {REQUEST_LOG_CHANNEL_ID})",
                ephemeral=True,
            )

        now_utc = datetime.now(timezone.utc)
        e = discord.Embed(
            title="📦 자원 요청 로그",
            description=(
                f"**요청자:** {interaction.user.mention}\n"
                f"**품목:** **{self.item_name}**\n"
                f"**수량:** **{qty}개**\n"
                f"**요청 채널:** {interaction.channel.mention if interaction.channel else '-'}\n"
                f"**요청 사유:**\n{reason}"
            ),
            color=0x57F287,
            timestamp=now_utc,
        )
        if guild.icon:
            e.set_thumbnail(url=guild.icon.url)

        _update_status_field(e, "⏳ 대기 중")
        _clear_done_footer(e)

        # ✅ 로그 메시지 전송 + 관리자 버튼 포함
        log_msg = await log_ch.send(embed=e, view=ResourceLogView(log_message_id=0, done=False))

        # message_id를 custom_id에 반영하기 위해 view 재부착
        if hasattr(interaction.client, "safe_editor"):
            await interaction.client.safe_editor.schedule_edit(
                log_msg,
                view=ResourceLogView(log_message_id=log_msg.id, done=False),
            )
        else:
            await log_msg.edit(view=ResourceLogView(log_message_id=log_msg.id, done=False))

        # ✅ 로그 인덱스 저장(재시작 복구용)
        idx = _load_log_index()
        ids = set(idx.get("log_message_ids", []))
        ids.add(log_msg.id)
        idx["log_message_ids"] = sorted(ids)
        _save_log_index(idx)

        await interaction.response.send_message(
            f"✅ 요청이 접수되었습니다. **{self.item_name} {qty}개** 요청 로그를 전송하였습니다.",
            ephemeral=True,
        )


# =========================
# 자원 선택 드롭다운
# =========================
class ResourceSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=item, value=item, emoji="📦") for item in RESOURCE_ITEMS]
        super().__init__(
            placeholder="요청하실 자원을 선택해 주세요",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="resource_select_temp",
        )

    async def callback(self, interaction: discord.Interaction):
        item = self.values[0]
        await interaction.response.send_modal(ResourceFormModal(item))


class ResourcePickView(discord.ui.View):
    """버튼 클릭 후 표시되는(일시적) 선택 뷰"""
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(ResourceSelect())


# =========================
# 메인(요청) 버튼: Persistent
# =========================
class ResourceRequestButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="📦 자원 요청",
            style=discord.ButtonStyle.primary,
            custom_id=PERSISTENT_REQUEST_BUTTON_ID,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "요청하실 자원을 선택해 주세요.",
            view=ResourcePickView(),
            ephemeral=True,
        )


class ResourceRequestView(discord.ui.View):
    """Persistent View: 봇 재시작 후에도 요청 버튼 동작"""
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(ResourceRequestButton())


# =========================
# 관리자 처리 완료/취소 버튼(로그용)
# =========================
class MarkDoneButton(discord.ui.Button):
    def __init__(self, log_message_id: int, disabled: bool):
        super().__init__(
            label="✅ 처리 완료",
            style=discord.ButtonStyle.success,
            custom_id=f"resource_done_{log_message_id}",
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("❌ 길드에서만 사용하실 수 있습니다.", ephemeral=True)

        if not _is_admin(interaction.user):
            return await interaction.response.send_message("❌ 관리자만 처리 완료로 변경하실 수 있습니다.", ephemeral=True)

        msg = interaction.message
        if not msg or not msg.embeds:
            return await interaction.response.send_message("❌ 처리할 로그를 찾을 수 없습니다.", ephemeral=True)

        embed = msg.embeds[0].copy()
        if _has_done(embed):
            return await interaction.response.send_message("ℹ️ 이미 처리 완료된 요청입니다.", ephemeral=True)

        done_at_kst_dt = datetime.now(timezone.utc).astimezone(KST)
        done_at_kst = done_at_kst_dt.strftime("%Y-%m-%d %H:%M")

        done_value = (
            "✅ 완료\n"
            f"**처리자:** {interaction.user.mention}\n"
            f"**처리 시각(KST):** {done_at_kst}"
        )

        _update_status_field(embed, done_value)
        _set_done_footer(embed, by_user_id=interaction.user.id, done_at_kst=done_at_kst)

        embed.color = 0x2ECC71
        if embed.title and not embed.title.startswith("✅ "):
            embed.title = f"✅ {embed.title}"

        if hasattr(interaction.client, "safe_editor"):
            await interaction.client.safe_editor.schedule_edit(
                msg,
                embed=embed,
                view=ResourceLogView(log_message_id=msg.id, done=True),
            )
        else:
            await msg.edit(embed=embed, view=ResourceLogView(log_message_id=msg.id, done=True))
        await interaction.response.send_message("✅ 처리 완료로 변경하였습니다.", ephemeral=True)


class UndoDoneButton(discord.ui.Button):
    def __init__(self, log_message_id: int, disabled: bool):
        super().__init__(
            label="↩️ 처리 취소",
            style=discord.ButtonStyle.secondary,
            custom_id=f"resource_undo_{log_message_id}",
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("❌ 길드에서만 사용하실 수 있습니다.", ephemeral=True)

        msg = interaction.message
        if not msg or not msg.embeds:
            return await interaction.response.send_message("❌ 처리할 로그를 찾을 수 없습니다.", ephemeral=True)

        embed = msg.embeds[0].copy()
        if not _has_done(embed):
            return await interaction.response.send_message("ℹ️ 현재 완료 상태가 아닙니다.", ephemeral=True)

        done_by = _get_done_by_user_id(embed)
        if done_by is None:
            return await interaction.response.send_message("❌ 처리자 정보가 없어 취소할 수 없습니다.", ephemeral=True)

        # ✅ 처리자만 취소 가능
        if interaction.user.id != done_by:
            return await interaction.response.send_message("❌ 처리 취소는 처리자 본인만 가능합니다.", ephemeral=True)

        _update_status_field(embed, "⏳ 대기 중")
        _clear_done_footer(embed)

        embed.color = 0x57F287
        if embed.title and embed.title.startswith("✅ "):
            embed.title = embed.title.replace("✅ ", "", 1)

        if hasattr(interaction.client, "safe_editor"):
            await interaction.client.safe_editor.schedule_edit(
                msg,
                embed=embed,
                view=ResourceLogView(log_message_id=msg.id, done=False),
            )
        else:
            await msg.edit(embed=embed, view=ResourceLogView(log_message_id=msg.id, done=False))
        await interaction.response.send_message("↩️ 처리 완료 상태를 취소하였습니다.", ephemeral=True)


class ResourceLogView(discord.ui.View):
    """로그 메시지용 View (message_id 기반 custom_id 사용)"""
    def __init__(self, log_message_id: int, done: bool):
        super().__init__(timeout=None)
        self.add_item(MarkDoneButton(log_message_id=log_message_id, disabled=done))
        self.add_item(UndoDoneButton(log_message_id=log_message_id, disabled=not done))


# =========================
# COG
# =========================
class ResourceRequestCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # ✅ 재시작 시: 기존 로그 메시지들에 View 재부착
        await self._reattach_log_views()

    async def _reattach_log_views(self):
        idx = _load_log_index()
        msg_ids: List[int] = idx.get("log_message_ids", [])
        if not msg_ids:
            return

        for guild in self.bot.guilds:
            ch = guild.get_channel(REQUEST_LOG_CHANNEL_ID)
            if not ch:
                try:
                    ch = await guild.fetch_channel(REQUEST_LOG_CHANNEL_ID)
                except Exception:
                    ch = None

            if not isinstance(ch, (discord.TextChannel, discord.Thread)):
                continue

            kept: List[int] = []
            for mid in msg_ids:
                try:
                    m = await ch.fetch_message(mid)
                    done = False
                    if m.embeds:
                        done = _has_done(m.embeds[0])

                    if hasattr(self.bot, "safe_editor"):
                        await self.bot.safe_editor.schedule_edit(
                            m,
                            view=ResourceLogView(log_message_id=mid, done=done),
                        )
                    else:
                        await m.edit(view=ResourceLogView(log_message_id=mid, done=done))
                    kept.append(mid)
                except Exception:
                    continue

            idx["log_message_ids"] = kept
            _save_log_index(idx)

    @commands.command(name="자원요청")
    async def resource_request(self, ctx: commands.Context):
        """!자원요청 → 임베드 + 자원 요청 버튼 생성"""
        if not ctx.guild:
            return

        e = discord.Embed(
            title="📦 자원 요청",
            description=(
                "아래 버튼을 눌러 **요청하실 자원**, **수량**, **요청 사유**를 입력해 주세요.\n"
                f"요청 가능 품목: **{', '.join(RESOURCE_ITEMS)}**\n\n"
                "요청 로그는 지정된 채널로 자동 전송됩니다."
            ),
            color=0x5865F2,
        )
        if ctx.guild.icon:
            e.set_thumbnail(url=ctx.guild.icon.url)

        # ✅ 요청 임베드 이미지
        e.set_image(url=REQUEST_EMBED_IMAGE_URL)

        await ctx.send(embed=e, view=ResourceRequestView())


async def setup(bot: commands.Bot):
    await bot.add_cog(ResourceRequestCog(bot))
