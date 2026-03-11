# cogs/anonymous_poll.py
import json
import uuid
import asyncio
import logging
import pathlib
import calendar
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Literal, Any, List

import discord
from discord.ext import commands, tasks
from discord import app_commands

log = logging.getLogger("SDT-BOT")

VoteSide = Literal["YES", "NO"]

# =========================
# Paths / Storage
# =========================
BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
POLL_DB_PATH = DATA_DIR / "anonymous_polls.json"

KST = timezone(timedelta(hours=9))

# =========================
# Time Parse (admin modify용)
# =========================
DUR_RE = re.compile(r"^\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*$", re.IGNORECASE)
ABS_RE = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})\s*$")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def parse_end_time(raw: str) -> datetime:
    raw = raw.strip()

    m = ABS_RE.match(raw)
    if m:
        y, mo, d, hh, mm = map(int, m.groups())
        dt_kst = datetime(y, mo, d, hh, mm, tzinfo=KST)
        return dt_kst.astimezone(timezone.utc)

    m = DUR_RE.match(raw)
    if m:
        h = int(m.group(1) or 0)
        mi = int(m.group(2) or 0)
        if h == 0 and mi == 0:
            raise ValueError("duration이 0입니다.")
        return now_utc() + timedelta(hours=h, minutes=mi)

    raise ValueError("시간 형식이 올바르지 않습니다. 예) 30m / 2h / 1h30m / 2026-01-23 21:30")


def format_remaining(end_at_utc: datetime) -> str:
    delta = end_at_utc - now_utc()
    if delta.total_seconds() <= 0:
        return "0시간 0분"
    total_minutes = int(delta.total_seconds() // 60)
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours}시간 {minutes}분"


def is_admin(member: discord.Member) -> bool:
    perms = member.guild_permissions
    return perms.administrator or perms.manage_guild or perms.manage_messages


def pretty_gauge(yes: int, no: int, width: int = 24) -> str:
    """🟩(찬성) + 🟥(반대) 한 줄 게이지"""
    total = yes + no
    if total <= 0:
        return "⬛" * width

    ratio = yes / total
    yes_blocks = int(round(ratio * width))

    # 극단 라운딩 보정(둘 다 표가 있으면 최소 1칸은 남겨주기)
    if yes > 0 and yes_blocks == 0:
        yes_blocks = 1
    if no > 0 and yes_blocks == width:
        yes_blocks = width - 1

    return ("🟩" * yes_blocks) + ("🟥" * (width - yes_blocks))


# =========================
# Data Model
# =========================
@dataclass
class VoteRecord:
    side: VoteSide
    reason: str
    at_iso: str


@dataclass
class PollState:
    poll_id: str
    guild_id: int
    channel_id: int
    message_id: int

    question: str
    created_by: int
    created_at_iso: str
    end_at_iso: str  # UTC ISO

    is_closed: bool = False
    closed_at_iso: Optional[str] = None
    closed_by: Optional[int] = None

    votes: Dict[str, VoteRecord] = field(default_factory=dict)  # user_id -> VoteRecord


# =========================
# JSON Storage
# =========================
def _load_db() -> Dict[str, Any]:
    if not POLL_DB_PATH.exists():
        return {}
    try:
        return json.loads(POLL_DB_PATH.read_text(encoding="utf-8"))
    except Exception:
        log.exception("anonymous_polls.json 로드 실패")
        return {}


def _save_db(db: Dict[str, Any]) -> None:
    tmp = POLL_DB_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(POLL_DB_PATH)


def state_to_dict(s: PollState) -> Dict[str, Any]:
    return {
        "poll_id": s.poll_id,
        "guild_id": s.guild_id,
        "channel_id": s.channel_id,
        "message_id": s.message_id,
        "question": s.question,
        "created_by": s.created_by,
        "created_at_iso": s.created_at_iso,
        "end_at_iso": s.end_at_iso,
        "is_closed": s.is_closed,
        "closed_at_iso": s.closed_at_iso,
        "closed_by": s.closed_by,
        "votes": {
            uid: {"side": v.side, "reason": v.reason, "at_iso": v.at_iso}
            for uid, v in s.votes.items()
        },
    }


def dict_to_state(d: Dict[str, Any]) -> PollState:
    s = PollState(
        poll_id=d["poll_id"],
        guild_id=int(d["guild_id"]),
        channel_id=int(d["channel_id"]),
        message_id=int(d["message_id"]),
        question=d["question"],
        created_by=int(d["created_by"]),
        created_at_iso=d["created_at_iso"],
        end_at_iso=d["end_at_iso"],
        is_closed=bool(d.get("is_closed", False)),
        closed_at_iso=d.get("closed_at_iso"),
        closed_by=d.get("closed_by"),
        votes={},
    )
    votes = d.get("votes", {}) or {}
    for uid, vd in votes.items():
        s.votes[str(uid)] = VoteRecord(
            side=vd["side"],
            reason=vd["reason"],
            at_iso=vd["at_iso"],
        )
    return s


# =========================
# Embeds
# - title: 안건(제목)
# - description: 투표 진행 상황
# - thumbnail: 길드(서버) 아이콘
# =========================
def build_poll_embed_active(state: PollState, guild_icon_url: Optional[str]) -> discord.Embed:
    end_at = datetime.fromisoformat(state.end_at_iso)
    participants = len(state.votes)

    e = discord.Embed(
        title=f"📢 {state.question}",
        description=(
            "🟦 **투표 진행중**\n"
            "아래 버튼으로 **찬성/반대 + 사유**를 등록하세요.\n"
            "※ 진행 중에는 **사유/찬반 수치 비공개**, **총 참여 인원만 표시**\n"
            "※ **사유는 투표 종료 후** 익명으로 공개됩니다."
        ),
        color=0x5865F2,
    )
    if guild_icon_url:
        e.set_thumbnail(url=guild_icon_url)

    e.add_field(name="⏰ 종료 시각", value=f"{discord.utils.format_dt(end_at, style='F')}", inline=True)
    e.add_field(name="⌛ 남은 시간", value=f"**{format_remaining(end_at)}**", inline=True)
    e.add_field(name="👥 총 참여 인원", value=f"**{participants}명**", inline=True)
    e.set_footer(text=f"Poll ID: {state.poll_id}  |  1분마다 자동 갱신")
    return e


def build_poll_embed_closed(state: PollState, guild_icon_url: Optional[str]) -> discord.Embed:
    end_at = datetime.fromisoformat(state.end_at_iso)
    total_participants = len(state.votes)

    yes = sum(1 for v in state.votes.values() if v.side == "YES")
    no = sum(1 for v in state.votes.values() if v.side == "NO")
    total_votes = yes + no

    yes_pct = (yes / total_votes * 100.0) if total_votes else 0.0
    no_pct = (no / total_votes * 100.0) if total_votes else 0.0

    if yes > no:
        result = "✅ **찬성 우세**"
        result_color = 0x57F287
    elif no > yes:
        result = "❌ **반대 우세**"
        result_color = 0xED4245
    else:
        result = "➗ **동률**"
        result_color = 0xFEE75C

    bar = pretty_gauge(yes, no, width=24)

    e = discord.Embed(
        title=f"📢 {state.question}",
        description="🟥 **투표 종료**\n투표가 종료되었습니다. 결과를 공개합니다.",
        color=result_color,
    )
    if guild_icon_url:
        e.set_thumbnail(url=guild_icon_url)

    e.add_field(name="⏰ 종료 시각", value=f"{discord.utils.format_dt(end_at, style='F')}", inline=True)
    e.add_field(name="👥 총 참여", value=f"**{total_participants}명**", inline=True)

    e.add_field(
        name="📊 결과(게이지)",
        value=(
            f"{bar}\n"
            f"찬성 **{yes_pct:.1f}%** ({yes})  |  반대 **{no_pct:.1f}%** ({no})\n"
            f"{result}"
        ),
        inline=False,
    )

    yes_reasons = [v.reason.strip() for v in state.votes.values() if v.side == "YES" and v.reason.strip()]
    no_reasons = [v.reason.strip() for v in state.votes.values() if v.side == "NO" and v.reason.strip()]

    def fmt_reasons(arr: List[str]) -> str:
        if not arr:
            return "없음"
        shown = arr[:20]
        lines = [f"• {t}" for t in shown]
        more = len(arr) - len(shown)
        if more > 0:
            lines.append(f"\n… 외 {more}개")
        return "\n".join(lines)[:1024]

    e.add_field(name="✅ 찬성 사유(익명)", value=fmt_reasons(yes_reasons), inline=False)
    e.add_field(name="❌ 반대 사유(익명)", value=fmt_reasons(no_reasons), inline=False)

    e.set_footer(text=f"Poll ID: {state.poll_id}")
    return e


# =========================
# Modals
# =========================
class VoteReasonModal(discord.ui.Modal):
    def __init__(self, cog: "AnonymousPollCog", poll_id: str, side: VoteSide):
        super().__init__(title="익명 투표 사유 작성")
        self.cog = cog
        self.poll_id = poll_id
        self.side = side

        self.reason = discord.ui.TextInput(
            label="사유(필수)",
            placeholder="투표 종료 후 익명으로 공개됩니다.",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=300,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.submit_vote(
            interaction=interaction,
            poll_id=self.poll_id,
            side=self.side,
            reason=str(self.reason.value).strip(),
        )


class ModifyPollModal(discord.ui.Modal):
    def __init__(self, cog: "AnonymousPollCog", poll_id: str, current_question: str):
        super().__init__(title="투표 수정(관리자)")
        self.cog = cog
        self.poll_id = poll_id

        self.question = discord.ui.TextInput(
            label="안건(비우면 유지)",
            style=discord.TextStyle.paragraph,
            required=False,
            default=current_question[:400],
            max_length=400,
        )
        self.end_time = discord.ui.TextInput(
            label="종료 시간(비우면 유지)",
            placeholder="예) 30m / 2h / 1h30m / 2026-01-23 21:30 (KST)",
            required=False,
            max_length=40,
        )
        self.add_item(self.question)
        self.add_item(self.end_time)

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.modify_poll(
            interaction=interaction,
            poll_id=self.poll_id,
            new_question=str(self.question.value).strip(),
            new_end_raw=str(self.end_time.value).strip(),
        )


class CreateVoteModal(discord.ui.Modal):
    """
    /vote 생성 모달
    - 년(YYYY)
    - 월/일 (MM/DD)
    - 시간/분 (HH:MM)
    """
    def __init__(self, cog: "AnonymousPollCog"):
        super().__init__(title="투표 생성")
        self.cog = cog

        self.question = discord.ui.TextInput(
            label="안건(제목/설명)",
            placeholder="예) 항구전 참여를 의무로 할까요?",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=400,
        )
        self.year = discord.ui.TextInput(
            label="년(YYYY)",
            placeholder="예) 2026",
            required=True,
            max_length=4,
        )
        self.month_day = discord.ui.TextInput(
            label="월/일 (MM/DD)",
            placeholder="예) 01/23 또는 1-23",
            required=True,
            max_length=10,
        )
        self.hour_min = discord.ui.TextInput(
            label="시간/분 (HH:MM)",
            placeholder="예) 19:30 또는 19 30",
            required=True,
            max_length=10,
        )

        self.add_item(self.question)
        self.add_item(self.year)
        self.add_item(self.month_day)
        self.add_item(self.hour_min)

    def _to_int(self, s: str) -> int:
        s = s.strip()
        if not s.isdigit():
            raise ValueError("숫자만 입력해주세요.")
        return int(s)

    def _parse_month_day(self, s: str) -> tuple[int, int]:
        s = s.strip()
        parts = re.split(r"[\/\-\.\s]+", s)
        if len(parts) != 2:
            raise ValueError("월/일 형식이 올바르지 않습니다. 예) 01/23")
        mo = self._to_int(parts[0])
        d = self._to_int(parts[1])
        return mo, d

    def _parse_hour_min(self, s: str) -> tuple[int, int]:
        s = s.strip()
        parts = re.split(r"[:\s]+", s)
        if len(parts) != 2:
            raise ValueError("시간/분 형식이 올바르지 않습니다. 예) 19:30")
        hh = self._to_int(parts[0])
        mm = self._to_int(parts[1])
        return hh, mm

    async def on_submit(self, interaction: discord.Interaction):
        try:
            q = str(self.question.value).strip()
            y = self._to_int(str(self.year.value))

            mo, d = self._parse_month_day(str(self.month_day.value))
            hh, mm = self._parse_hour_min(str(self.hour_min.value))

            if not (2020 <= y <= 2100):
                raise ValueError("년은 2020~2100 범위로 입력해주세요.")
            if not (1 <= mo <= 12):
                raise ValueError("월은 1~12 범위로 입력해주세요.")

            last = days_in_month(y, mo)
            if not (1 <= d <= last):
                raise ValueError(f"일은 1~{last} 범위로 입력해주세요.")
            if not (0 <= hh <= 23):
                raise ValueError("시간은 0~23 범위로 입력해주세요.")
            if not (0 <= mm <= 59):
                raise ValueError("분은 0~59 범위로 입력해주세요.")

            end_kst = datetime(y, mo, d, hh, mm, tzinfo=KST)
            end_utc = end_kst.astimezone(timezone.utc)

            if end_utc <= now_utc():
                raise ValueError("종료 시간이 현재보다 과거입니다. 미래 시간으로 입력해주세요.")

        except Exception as e:
            await interaction.response.send_message(f"❌ 입력 오류: {e}", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        poll_id = await self.cog.create_poll_message_with_end_iso(
            guild=interaction.guild,
            channel=interaction.channel,
            created_by=interaction.user,
            question=q,
            end_iso_utc=end_utc.isoformat(),
        )
        if not poll_id:
            await interaction.followup.send("❌ 투표 생성 실패", ephemeral=True)
            return

        await interaction.followup.send(f"✅ 투표 생성 완료! (Poll ID: `{poll_id}`)", ephemeral=True)


# =========================
# Vote View (persistent)
# =========================
class PollVoteView(discord.ui.View):
    def __init__(self, cog: "AnonymousPollCog", poll_id: str, closed: bool):
        super().__init__(timeout=None)
        self.cog = cog
        self.poll_id = poll_id
        self.closed = closed
        self._add_buttons()

    def _add_buttons(self):
        yes_btn = discord.ui.Button(
            label="찬성",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id=f"anonpoll:{self.poll_id}:yes",
        )
        no_btn = discord.ui.Button(
            label="반대",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"anonpoll:{self.poll_id}:no",
        )
        mod_btn = discord.ui.Button(
            label="투표 수정",
            style=discord.ButtonStyle.secondary,
            emoji="🛠️",
            custom_id=f"anonpoll:{self.poll_id}:modify",
        )
        end_btn = discord.ui.Button(
            label="투표 종료",
            style=discord.ButtonStyle.danger,
            emoji="⛔",
            custom_id=f"anonpoll:{self.poll_id}:end",
        )

        yes_btn.callback = self._on_yes
        no_btn.callback = self._on_no
        mod_btn.callback = self._on_modify
        end_btn.callback = self._on_end

        self.add_item(yes_btn)
        self.add_item(no_btn)
        self.add_item(mod_btn)
        self.add_item(end_btn)

        if self.closed:
            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True

    async def _on_yes(self, interaction: discord.Interaction):
        if self.closed:
            await interaction.response.send_message("이미 종료된 투표입니다.", ephemeral=True)
            return
        await interaction.response.send_modal(VoteReasonModal(self.cog, self.poll_id, "YES"))

    async def _on_no(self, interaction: discord.Interaction):
        if self.closed:
            await interaction.response.send_message("이미 종료된 투표입니다.", ephemeral=True)
            return
        await interaction.response.send_modal(VoteReasonModal(self.cog, self.poll_id, "NO"))

    async def _on_modify(self, interaction: discord.Interaction):
        if self.closed:
            await interaction.response.send_message("이미 종료된 투표입니다.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            await interaction.response.send_message("관리자만 수정할 수 있습니다.", ephemeral=True)
            return
        st = self.cog.polls.get(self.poll_id)
        if not st:
            await interaction.response.send_message("투표 정보를 찾을 수 없습니다.", ephemeral=True)
            return
        await interaction.response.send_modal(ModifyPollModal(self.cog, self.poll_id, st.question))

    async def _on_end(self, interaction: discord.Interaction):
        if self.closed:
            await interaction.response.send_message("이미 종료된 투표입니다.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            await interaction.response.send_message("관리자만 종료할 수 있습니다.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ok = await self.cog.close_poll(self.poll_id, closed_by=interaction.user.id, reason="admin_end")
        await interaction.followup.send("투표를 종료했습니다." if ok else "종료 실패(메시지/채널 확인).", ephemeral=True)


# =========================
# Cog
# =========================
class AnonymousPollCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.polls: Dict[str, PollState] = {}
        self._lock = asyncio.Lock()

        self._load_states()
        self.poll_tick.start()

        # 재시작 복구: persistent view 재등록
        for pid, st in self.polls.items():
            self.bot.add_view(PollVoteView(self, pid, closed=st.is_closed))

    def cog_unload(self):
        self.poll_tick.cancel()

    # ---------- DB ----------
    def _load_states(self):
        db = _load_db()
        self.polls.clear()
        for pid, d in db.items():
            try:
                self.polls[pid] = dict_to_state(d)
            except Exception:
                log.exception("PollState 로드 실패: %s", pid)

    def _persist(self):
        db = {pid: state_to_dict(st) for pid, st in self.polls.items()}
        _save_db(db)

    # ---------- helpers ----------
    def _guild_icon_url(self, guild_id: int) -> Optional[str]:
        g = self.bot.get_guild(guild_id)
        if g and g.icon:
            return g.icon.url
        return None

    # ---------- fetch/edit ----------
    async def _fetch_message(self, state: PollState) -> Optional[discord.Message]:
        guild = self.bot.get_guild(state.guild_id)
        if not guild:
            return None
        ch = guild.get_channel(state.channel_id)
        if not isinstance(ch, (discord.TextChannel, discord.Thread)):
            try:
                ch = await self.bot.fetch_channel(state.channel_id)
            except Exception:
                return None
        try:
            return await ch.fetch_message(state.message_id)
        except Exception:
            return None

    async def _edit_poll_message(self, state: PollState) -> bool:
        msg = await self._fetch_message(state)
        if not msg:
            return False

        icon_url = self._guild_icon_url(state.guild_id)
        embed = build_poll_embed_closed(state, icon_url) if state.is_closed else build_poll_embed_active(state, icon_url)

        view = PollVoteView(self, state.poll_id, closed=state.is_closed)
        self.bot.add_view(view)

        try:
            if hasattr(self.bot, "safe_editor"):
                await self.bot.safe_editor.schedule_edit(
                    msg,
                    embed=embed,
                    view=view,
                )
            else:
                await msg.edit(embed=embed, view=view)
            return True
        except Exception:
            log.exception("poll message edit 실패")
            return False

    # ---------- create ----------
    async def create_poll_message_with_end_iso(
        self,
        guild: discord.Guild,
        channel: discord.abc.Messageable,
        created_by: discord.abc.User,
        question: str,
        end_iso_utc: str,
    ) -> Optional[str]:
        try:
            end_at = datetime.fromisoformat(end_iso_utc)
            if end_at <= now_utc():
                raise ValueError("종료 시간이 현재보다 과거입니다.")
        except Exception as e:
            try:
                await channel.send(f"❌ 종료 시간 오류: {e}")
            except Exception:
                pass
            return None

        poll_id = uuid.uuid4().hex[:10]
        created_at = now_utc()

        state = PollState(
            poll_id=poll_id,
            guild_id=guild.id,
            channel_id=getattr(channel, "id", 0),
            message_id=0,
            question=question[:256],  # 타이틀 길이 안전
            created_by=created_by.id,
            created_at_iso=created_at.isoformat(),
            end_at_iso=end_iso_utc,
        )

        icon_url = guild.icon.url if guild.icon else None
        embed = build_poll_embed_active(state, icon_url)
        view = PollVoteView(self, poll_id, closed=False)

        try:
            msg = await channel.send(embed=embed, view=view)
        except Exception:
            log.exception("투표 메시지 전송 실패")
            return None

        state.message_id = msg.id
        if state.channel_id == 0:
            state.channel_id = msg.channel.id

        async with self._lock:
            self.polls[poll_id] = state
            self._persist()

        self.bot.add_view(view)
        return poll_id

    # ---------- voting ----------
    async def submit_vote(self, interaction: discord.Interaction, poll_id: str, side: VoteSide, reason: str):
        async with self._lock:
            st = self.polls.get(poll_id)
            if not st:
                await interaction.response.send_message("투표를 찾을 수 없습니다.", ephemeral=True)
                return
            if st.is_closed:
                await interaction.response.send_message("이미 종료된 투표입니다.", ephemeral=True)
                return

            end_at = datetime.fromisoformat(st.end_at_iso)
            if now_utc() >= end_at:
                await interaction.response.send_message("이미 마감되었습니다. 종료 처리 중입니다.", ephemeral=True)
                await self.close_poll(poll_id, closed_by=None, reason="auto_timeout")
                return

            uid = str(interaction.user.id)
            st.votes[uid] = VoteRecord(side=side, reason=reason, at_iso=now_utc().isoformat())
            self._persist()

        await self._edit_poll_message(st)
        await interaction.response.send_message("익명으로 등록되었습니다. (사유는 종료 후 공개)", ephemeral=True)

    # ---------- modify ----------
    async def modify_poll(self, interaction: discord.Interaction, poll_id: str, new_question: str, new_end_raw: str):
        if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            await interaction.response.send_message("관리자만 수정할 수 있습니다.", ephemeral=True)
            return

        async with self._lock:
            st = self.polls.get(poll_id)
            if not st:
                await interaction.response.send_message("투표를 찾을 수 없습니다.", ephemeral=True)
                return
            if st.is_closed:
                await interaction.response.send_message("이미 종료된 투표입니다.", ephemeral=True)
                return

            if new_question:
                st.question = new_question[:256]

            if new_end_raw:
                try:
                    new_end = parse_end_time(new_end_raw)
                    if new_end <= now_utc():
                        raise ValueError("종료 시간이 현재보다 과거입니다.")
                    st.end_at_iso = new_end.isoformat()
                except Exception as e:
                    await interaction.response.send_message(f"종료 시간 형식 오류: {e}", ephemeral=True)
                    return

            self._persist()

        ok = await self._edit_poll_message(st)
        await interaction.response.send_message("투표를 수정했습니다." if ok else "수정은 됐지만 메시지 갱신 실패", ephemeral=True)

    # ---------- close ----------
    async def close_poll(self, poll_id: str, closed_by: Optional[int], reason: str = "auto") -> bool:
        async with self._lock:
            st = self.polls.get(poll_id)
            if not st:
                return False
            if st.is_closed:
                return True

            st.is_closed = True
            st.closed_at_iso = now_utc().isoformat()
            st.closed_by = closed_by
            self._persist()

        # ✅ 중복 임베드 전송 없음: 원본 메시지 edit만
        return await self._edit_poll_message(st)

    # ---------- tick(1분) ----------
    @tasks.loop(seconds=60)
    async def poll_tick(self):
        to_update: List[PollState] = []
        to_close: List[str] = []

        async with self._lock:
            for pid, st in self.polls.items():
                if st.is_closed:
                    continue
                end_at = datetime.fromisoformat(st.end_at_iso)
                if now_utc() >= end_at:
                    to_close.append(pid)
                else:
                    to_update.append(st)

        for pid in to_close:
            await self.close_poll(pid, closed_by=None, reason="auto_timeout")

        for st in to_update:
            await self._edit_poll_message(st)

    @poll_tick.before_loop
    async def before_poll_tick(self):
        await self.bot.wait_until_ready()

    # =========================
    # Slash Command: /vote
    # =========================
    @app_commands.command(name="vote", description="익명 찬반 투표 생성")
    @app_commands.guild_only()
    async def slash_create(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CreateVoteModal(self))

    # (옵션) 관리자 텍스트 명령으로 강제 종료
    @commands.command(name="투표종료")
    @commands.guild_only()
    async def force_end(self, ctx: commands.Context, poll_id: str):
        if not isinstance(ctx.author, discord.Member) or not is_admin(ctx.author):
            await ctx.reply("관리자만 사용할 수 있습니다.", mention_author=False)
            return
        ok = await self.close_poll(poll_id, closed_by=ctx.author.id, reason="admin_cmd")
        await ctx.reply("종료 완료" if ok else "종료 실패", mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(AnonymousPollCog(bot))
