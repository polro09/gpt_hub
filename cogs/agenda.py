# cogs/agenda.py
# ✅ 제독/장교 회의 안건 시스템 (최종본)
#
# ✅ 요구사항 반영
# - /안건 (hybrid) : 모달로 "안건 제목/내용/마감(예: 3일 6시간뒤)" 입력
# - @here 사용 X → 메시지 content 에서만 역할 3개를 실제 멘션(핑)
# - 임베드는 표시용 정보만 포함(멘션 줄을 임베드에 넣지 않음)
# - 역할 버튼(Fleet Admiral / Admiral / Captain) Persistent View → 오래 지나거나 봇 재시작해도 동작
# - 찬성/반대/기권 + 의견(구 어구) 입력
#   출력 형식:
#     ✅ 찬성 — @이름
#     > 의견: ...
# - 미참여자는 아래에 직급별로 가로 배치(인라인 3칸)
# - tzdata 없어서 ZoneInfo 실패해도 동작(fallback timezone)
# - Opinion 반영 시 ephemeral message edit 금지:
#   항상 state.message_id(안건 원본 메시지)를 fetch 후 edit (404 Unknown Message 해결)
#
# ✅ main.py 필수
# from cogs.agenda import AgendaVoteView
# self.add_view(AgendaVoteView())

import json
import pathlib
import asyncio
import re
import discord
from discord.ext import commands
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
from datetime import datetime, timezone, timedelta

# =========================
# 역할 ID (사용자 제공)
# =========================
ROLE_FLEET_ADMIRAL = 1456634446659719271
ROLE_ADMIRAL       = 1456635035288342671
ROLE_CAPTAIN       = 1456635069157347399

ROLE_ORDER = [ROLE_FLEET_ADMIRAL, ROLE_ADMIRAL, ROLE_CAPTAIN]

# ✅ 실제 핑은 메시지 content에서만
ROLE_MENTION_LINE = f"<@&{ROLE_FLEET_ADMIRAL}> <@&{ROLE_ADMIRAL}> <@&{ROLE_CAPTAIN}>"

# =========================
# 저장 경로(재시작 복구)
# =========================
BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
AGENDA_STATE_PATH = DATA_DIR / "agenda_states.json"

# =========================
# TZ (ZoneInfo 우선, 실패 시 고정 오프셋 fallback)
# - tzdata 미설치 환경에서도 동작
# - DST(서머타임) 자동 반영은 ZoneInfo가 정상일 때만
# =========================
def _get_tz(name: str, fallback_offset_hours: int) -> timezone:
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return timezone(timedelta(hours=fallback_offset_hours))

KST = _get_tz("Asia/Seoul", +9)
ET  = _get_tz("America/New_York", -5)
PT  = _get_tz("America/Los_Angeles", -8)

# =========================
# 상태 모델
# =========================
@dataclass
class VoteEntry:
    role_id: int
    stance: str        # "AGREE" | "OPPOSE" | "ABSTAIN"
    opinion: str       # "의견"(구 어구)
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "role_id": self.role_id,
            "stance": self.stance,
            "opinion": self.opinion,
            "at": self.at.isoformat(),
        }

    @staticmethod
    def from_dict(d: dict) -> "VoteEntry":
        at_raw = d.get("at")
        try:
            at_dt = datetime.fromisoformat(at_raw) if at_raw else datetime.now(timezone.utc)
        except Exception:
            at_dt = datetime.now(timezone.utc)
        if at_dt.tzinfo is None:
            at_dt = at_dt.replace(tzinfo=timezone.utc)

        return VoteEntry(
            role_id=int(d["role_id"]),
            stance=str(d["stance"]),
            opinion=str(d.get("opinion", "")),
            at=at_dt,
        )


@dataclass
class AgendaState:
    guild_id: int
    channel_id: int
    message_id: int
    proposer_id: int
    title: str
    content: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    deadline_at: Optional[datetime] = None  # UTC
    deadline_raw: str = ""
    votes: Dict[int, VoteEntry] = field(default_factory=dict)  # user_id -> VoteEntry

    def to_dict(self) -> dict:
        return {
            "guild_id": self.guild_id,
            "channel_id": self.channel_id,
            "message_id": self.message_id,
            "proposer_id": self.proposer_id,
            "title": self.title,
            "content": self.content,
            "created_at": self.created_at.isoformat(),
            "deadline_at": self.deadline_at.isoformat() if self.deadline_at else None,
            "deadline_raw": self.deadline_raw,
            "votes": {str(uid): v.to_dict() for uid, v in self.votes.items()},
        }

    @staticmethod
    def from_dict(d: dict) -> "AgendaState":
        def _dt(v: Optional[str]) -> Optional[datetime]:
            if not v:
                return None
            try:
                x = datetime.fromisoformat(v)
            except Exception:
                return None
            if x.tzinfo is None:
                x = x.replace(tzinfo=timezone.utc)
            return x

        created_at = _dt(d.get("created_at")) or datetime.now(timezone.utc)
        deadline_at = _dt(d.get("deadline_at"))

        votes_raw = d.get("votes", {}) or {}
        votes: Dict[int, VoteEntry] = {}
        for k, vv in votes_raw.items():
            try:
                votes[int(k)] = VoteEntry.from_dict(vv)
            except Exception:
                continue

        return AgendaState(
            guild_id=int(d["guild_id"]),
            channel_id=int(d["channel_id"]),
            message_id=int(d["message_id"]),
            proposer_id=int(d["proposer_id"]),
            title=str(d["title"]),
            content=str(d["content"]),
            created_at=created_at,
            deadline_at=deadline_at,
            deadline_raw=str(d.get("deadline_raw", "")),
            votes=votes,
        )

# =========================
# 유틸
# =========================
def _has_role(member: discord.Member, role_id: int) -> bool:
    return any(r.id == role_id for r in member.roles)

def _cut(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else (s[:n] + "…")

def _fmt_vote_line(member: discord.Member, stance: str, opinion: str) -> str:
    # 기존 스타일: 선택지+이름 1줄, 의견은 바로 아래 줄
    if stance == "AGREE":
        head = f"✅ 찬성 — {member.mention}"
    elif stance == "OPPOSE":
        head = f"❌ 반대 — {member.mention}"
    else:
        head = f"➖ 기권 — {member.mention}"

    opinion = (opinion or "").strip()
    if opinion:
        opinion = _cut(opinion, 300)
        return f"{head}\n> 의견: {opinion}"
    return head

def _join_mentions_inline(ms: List[str], limit: int = 1000) -> str:
    # 미참여자 가로 배치
    if not ms:
        return "-"
    out = ""
    for m in ms:
        add = m + " "
        if len(out) + len(add) > limit:
            out += "…"
            break
        out += add
    return out.strip()

def build_gauge_line(agree: int, oppose: int, abstain: int, segments: int = 10) -> str:
    total = agree + oppose + abstain
    if total <= 0:
        return "⬛" * segments

    a = agree / total * segments
    o = oppose / total * segments
    b = abstain / total * segments

    a_i = int(round(a))
    o_i = int(round(o))
    b_i = int(round(b))

    s = a_i + o_i + b_i
    dif = segments - s

    if dif != 0:
        parts = [("A", a - a_i), ("O", o - o_i), ("B", b - b_i)]
        if dif > 0:
            parts.sort(key=lambda x: x[1], reverse=True)
            for i in range(dif):
                k = parts[i % 3][0]
                if k == "A":
                    a_i += 1
                elif k == "O":
                    o_i += 1
                else:
                    b_i += 1
        else:
            parts.sort(key=lambda x: x[1])
            for i in range(-dif):
                k = parts[i % 3][0]
                if k == "A" and a_i > 0:
                    a_i -= 1
                elif k == "O" and o_i > 0:
                    o_i -= 1
                elif k == "B" and b_i > 0:
                    b_i -= 1

    bar = ("🟩" * max(0, a_i)) + ("🟥" * max(0, o_i)) + ("🟨" * max(0, b_i))
    if len(bar) < segments:
        bar += "⬛" * (segments - len(bar))
    elif len(bar) > segments:
        bar = bar[:segments]
    return bar

# ---- 상대시간 파싱: "3일 6시간뒤" / "2시간 30분뒤" / "45분뒤" ----
_REL_RE = re.compile(
    r"^\s*(?:(?P<d>\d+)\s*일)?\s*"
    r"(?:(?P<h>\d+)\s*시간)?\s*"
    r"(?:(?P<m>\d+)\s*분)?\s*"
    r"(?:\s*(뒤|후))?\s*$"
)

def parse_relative_korean(s: str) -> Optional[timedelta]:
    raw = (s or "").strip()
    if not raw:
        return None
    m = _REL_RE.match(raw)
    if not m:
        return None
    d = int(m.group("d") or 0)
    h = int(m.group("h") or 0)
    mm = int(m.group("m") or 0)
    if d == 0 and h == 0 and mm == 0:
        return None
    return timedelta(days=d, hours=h, minutes=mm)

def fmt_deadline_lines(deadline_utc: Optional[datetime]) -> str:
    if not deadline_utc:
        return "마감 시간이 설정되지 않았습니다."
    if deadline_utc.tzinfo is None:
        deadline_utc = deadline_utc.replace(tzinfo=timezone.utc)

    kst = deadline_utc.astimezone(KST)
    et = deadline_utc.astimezone(ET)
    pt = deadline_utc.astimezone(PT)
    return (
        f"- 🇰🇷 **한국(KST):** {kst.strftime('%Y-%m-%d %H:%M')}\n"
        f"- 🇺🇸 **미국(ET):** {et.strftime('%Y-%m-%d %H:%M')}\n"
        f"- 🇺🇸 **미국(PT):** {pt.strftime('%Y-%m-%d %H:%M')}"
    )

# =========================
# 모달: 안건 생성
# =========================
class CreateAgendaModal(discord.ui.Modal, title="안건 발의"):
    agenda_title = discord.ui.TextInput(
        label="안건 제목",
        placeholder="예) 항구전 집합시간 10분 앞당김",
        max_length=80,
    )
    agenda_content = discord.ui.TextInput(
        label="안건 내용",
        placeholder="안건의 배경/목표/요청 사항을 적어주세요.",
        style=discord.TextStyle.paragraph,
        max_length=1500,
    )
    deadline = discord.ui.TextInput(
        label="마감(예: 3일 6시간뒤)",
        placeholder="예) 3일 6시간뒤 / 2시간 30분뒤 / 45분뒤",
        required=False,
        max_length=30,
    )

    def __init__(self, cog: "AgendaCog"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        title = str(self.agenda_title.value).strip()
        content = str(self.agenda_content.value).strip()
        deadline_raw = str(self.deadline.value).strip()

        deadline_at: Optional[datetime] = None
        if deadline_raw:
            td = parse_relative_korean(deadline_raw)
            if not td:
                return await interaction.response.send_message(
                    "❌ 마감 형식을 해석할 수 없습니다.\n"
                    "예) `3일 6시간뒤`, `2시간 30분뒤`, `45분뒤`",
                    ephemeral=True,
                )
            deadline_at = datetime.now(timezone.utc) + td

        embed = await self.cog.build_agenda_embed(
            guild=interaction.guild,
            proposer=interaction.user,
            title=title,
            content=content,
            state=None,
            deadline_at=deadline_at,
            deadline_raw=deadline_raw,
        )

        view = AgendaVoteView()
        # ✅ 실제 핑은 content에서만
        msg = await interaction.channel.send(content=ROLE_MENTION_LINE, embed=embed, view=view)

        state = AgendaState(
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            message_id=msg.id,
            proposer_id=interaction.user.id,
            title=title,
            content=content,
            deadline_at=deadline_at,
            deadline_raw=deadline_raw,
        )
        self.cog.agendas[msg.id] = state
        await self.cog.save_states()

        updated = await self.cog.build_agenda_embed(
            guild=interaction.guild,
            proposer=interaction.user,
            title=title,
            content=content,
            state=state,
            deadline_at=deadline_at,
            deadline_raw=deadline_raw,
        )
        if hasattr(interaction.client, "safe_editor"):
            await interaction.client.safe_editor.schedule_edit(
                msg,
                embed=updated,
                view=view,
            )
        else:
            await msg.edit(embed=updated, view=view)

        await interaction.response.send_message("✅ 안건이 생성되었습니다.", ephemeral=True)

# =========================
# 2단계: 찬성/반대/기권 선택 (임시 뷰)
# =========================
class StancePickView(discord.ui.View):
    def __init__(self, agenda_message_id: int, role_id: int, voter_id: int):
        super().__init__(timeout=60)
        self.agenda_message_id = agenda_message_id
        self.role_id = role_id
        self.voter_id = voter_id

    async def _open_opinion_modal(self, interaction: discord.Interaction, stance: str):
        if interaction.user.id != self.voter_id:
            return await interaction.response.send_message("❌ 본인만 사용 가능합니다.", ephemeral=True)

        await interaction.response.send_modal(
            OpinionModal(
                agenda_message_id=self.agenda_message_id,
                role_id=self.role_id,
                stance=stance,
            )
        )

    @discord.ui.button(label="✅ 찬성", style=discord.ButtonStyle.success)
    async def agree(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._open_opinion_modal(interaction, "AGREE")

    @discord.ui.button(label="❌ 반대", style=discord.ButtonStyle.danger)
    async def oppose(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._open_opinion_modal(interaction, "OPPOSE")

    @discord.ui.button(label="➖ 기권", style=discord.ButtonStyle.secondary)
    async def abstain(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._open_opinion_modal(interaction, "ABSTAIN")

# =========================
# 3단계: 의견 모달
# =========================
class OpinionModal(discord.ui.Modal, title="의견 첨부"):
    opinion = discord.ui.TextInput(
        label="의견(선택)",
        placeholder="예) 준비시간 확보가 필요합니다.",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=400,
    )

    def __init__(self, agenda_message_id: int, role_id: int, stance: str):
        super().__init__()
        self.agenda_message_id = agenda_message_id
        self.role_id = role_id
        self.stance = stance

    async def on_submit(self, interaction: discord.Interaction):
        cog: Optional["AgendaCog"] = interaction.client.get_cog("AgendaCog")  # type: ignore
        if not cog:
            return await interaction.response.send_message("❌ AgendaCog를 찾을 수 없습니다.", ephemeral=True)

        state = cog.agendas.get(self.agenda_message_id)
        if not state:
            return await interaction.response.send_message("❌ 안건 상태를 찾을 수 없습니다.", ephemeral=True)

        # 마감 체크
        if state.deadline_at and datetime.now(timezone.utc) > state.deadline_at:
            return await interaction.response.send_message("⏰ 이미 마감된 안건입니다.", ephemeral=True)

        # 역할 체크
        if not _has_role(interaction.user, self.role_id):
            role = interaction.guild.get_role(self.role_id)
            name = role.name if role else "해당 역할"
            return await interaction.response.send_message(f"❌ {name} 보유자만 가능합니다.", ephemeral=True)

        opinion = (self.opinion.value or "").strip()
        state.votes[interaction.user.id] = VoteEntry(
            role_id=self.role_id,
            stance=self.stance,
            opinion=opinion,
        )
        await cog.save_states()

        proposer = interaction.guild.get_member(state.proposer_id) or interaction.user
        new_embed = await cog.build_agenda_embed(
            guild=interaction.guild,
            proposer=proposer,
            title=state.title,
            content=state.content,
            state=state,
            deadline_at=state.deadline_at,
            deadline_raw=state.deadline_raw,
        )

        # ✅ 항상 "안건 원본 메시지"를 fetch 후 edit (ephemeral message edit 금지)
        try:
            ch = interaction.guild.get_channel(state.channel_id)
            if ch is None:
                ch = await interaction.guild.fetch_channel(state.channel_id)

            agenda_msg = await ch.fetch_message(state.message_id)  # type: ignore
            if hasattr(interaction.client, "safe_editor"):
                await interaction.client.safe_editor.schedule_edit(
                    agenda_msg,
                    embed=new_embed,
                    view=AgendaVoteView(),
                )
            else:
                await agenda_msg.edit(embed=new_embed, view=AgendaVoteView())

        except discord.NotFound:
            cog.agendas.pop(self.agenda_message_id, None)
            await cog.save_states()
            return await interaction.response.send_message(
                "❌ 안건 원본 메시지를 찾을 수 없습니다. (삭제되었을 수 있습니다)",
                ephemeral=True,
            )
        except discord.Forbidden:
            return await interaction.response.send_message(
                "❌ 권한이 없어 안건 메시지를 수정할 수 없습니다. (채널 권한/메시지 관리 권한 확인)",
                ephemeral=True,
            )
        except Exception:
            return await interaction.response.send_message(
                "❌ 반영 중 오류가 발생했습니다. (로그를 확인해 주세요)",
                ephemeral=True,
            )

        await interaction.response.send_message("✅ 반영되었습니다.", ephemeral=True)

# =========================
# 역할 버튼 뷰 (Persistent View)
# =========================
class AgendaVoteView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(RoleGateButton(ROLE_FLEET_ADMIRAL, "Fleet Admiral", discord.ButtonStyle.primary))
        self.add_item(RoleGateButton(ROLE_ADMIRAL, "Admiral", discord.ButtonStyle.secondary))
        self.add_item(RoleGateButton(ROLE_CAPTAIN, "Captain", discord.ButtonStyle.success))

class RoleGateButton(discord.ui.Button):
    def __init__(self, role_id: int, label: str, style: discord.ButtonStyle):
        super().__init__(label=label, style=style, custom_id=f"agenda_role_{role_id}")
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction):
        cog: Optional["AgendaCog"] = interaction.client.get_cog("AgendaCog")  # type: ignore
        if not cog:
            return await interaction.response.send_message("❌ AgendaCog를 찾을 수 없습니다.", ephemeral=True)

        agenda_message_id = interaction.message.id
        state = cog.agendas.get(agenda_message_id)
        if not state:
            return await interaction.response.send_message("❌ 이 안건은 추적 대상이 아닙니다.", ephemeral=True)

        if state.deadline_at and datetime.now(timezone.utc) > state.deadline_at:
            return await interaction.response.send_message("⏰ 이미 마감된 안건입니다.", ephemeral=True)

        if not _has_role(interaction.user, self.role_id):
            role = interaction.guild.get_role(self.role_id)
            name = role.name if role else "해당 역할"
            return await interaction.response.send_message(f"❌ {name} 보유자만 누를 수 있습니다.", ephemeral=True)

        await interaction.response.send_message(
            "찬성/반대/기권을 선택해 주세요.",
            view=StancePickView(
                agenda_message_id=agenda_message_id,
                role_id=self.role_id,
                voter_id=interaction.user.id,
            ),
            ephemeral=True,
        )

# =========================
# COG
# =========================
class AgendaCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.agendas: Dict[int, AgendaState] = {}
        self._io_lock = asyncio.Lock()

    async def cog_load(self):
        await self.load_states()

    async def load_states(self):
        async with self._io_lock:
            if not AGENDA_STATE_PATH.exists():
                self.agendas = {}
                return
            try:
                raw = AGENDA_STATE_PATH.read_text(encoding="utf-8")
                data = json.loads(raw) if raw else {}
            except Exception:
                self.agendas = {}
                return

            items = data.get("agendas", {}) if isinstance(data, dict) else {}
            agendas: Dict[int, AgendaState] = {}
            for mid_str, payload in items.items():
                try:
                    st = AgendaState.from_dict(payload)
                    agendas[int(mid_str)] = st
                except Exception:
                    continue
            self.agendas = agendas

    async def save_states(self):
        async with self._io_lock:
            payload = {"agendas": {str(mid): st.to_dict() for mid, st in self.agendas.items()}}
            AGENDA_STATE_PATH.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    async def build_agenda_embed(
        self,
        guild: discord.Guild,
        proposer: discord.abc.User,
        title: str,
        content: str,
        state: Optional[AgendaState],
        deadline_at: Optional[datetime],
        deadline_raw: str,
    ) -> discord.Embed:
        e = discord.Embed(
            title=f"📌 안건: {title}",
            description=f"**발의자:** {proposer.mention}\n**내용:** {content}",
            color=0x5865F2,
        )
        if guild.icon:
            e.set_thumbnail(url=guild.icon.url)

        if deadline_at:
            e.add_field(
                name=f"⏱️ 마감 시간 입력: `{deadline_raw}`",
                value=fmt_deadline_lines(deadline_at),
                inline=False,
            )

        if not state:
            e.add_field(
                name="참여 방법",
                value="아래에서 **본인 역할 버튼**을 누른 뒤 **찬성/반대/기권**을 선택하고 **의견(선택)**을 작성해 주세요.",
                inline=False,
            )
            return e

        # 투표 요약 + 게이지
        agree = sum(1 for v in state.votes.values() if v.stance == "AGREE")
        oppose = sum(1 for v in state.votes.values() if v.stance == "OPPOSE")
        abstain = sum(1 for v in state.votes.values() if v.stance == "ABSTAIN")
        total = agree + oppose + abstain

        e.add_field(
            name="📊 투표 현황",
            value=f"**찬성 {agree} / 반대 {oppose} / 기권 {abstain} (총 {total})**\n{build_gauge_line(agree, oppose, abstain, 10)}",
            inline=False,
        )

        # 역할별 참여 표시(기존 스타일)
        non_participants_by_role: Dict[int, List[str]] = {}
        order_map = {"AGREE": 0, "OPPOSE": 1, "ABSTAIN": 2}

        for role_id in ROLE_ORDER:
            role = guild.get_role(role_id)
            role_name = role.name if role else str(role_id)

            role_members = [m for m in guild.members if (not m.bot) and _has_role(m, role_id)]

            entries: List[Tuple[discord.Member, VoteEntry]] = []
            for uid, v in state.votes.items():
                if v.role_id != role_id:
                    continue
                mem = guild.get_member(uid)
                if not mem:
                    continue
                entries.append((mem, v))

            entries.sort(key=lambda x: (order_map.get(x[1].stance, 9), x[1].at))

            participated_ids = {m.id for (m, _) in entries}
            non_participants_by_role[role_id] = [m.mention for m in role_members if m.id not in participated_ids]

            lines: List[str] = [_fmt_vote_line(mem, v.stance, v.opinion) for mem, v in entries]
            participated_count = len(participated_ids)
            total_count = len(role_members)

            e.add_field(
                name=f"👤 {role_name} ({participated_count}/{total_count} 참여)",
                value="\n".join(lines) if lines else "-",
                inline=False,
            )

        # 미참여자(직급별) - 아래쪽 가로 배치(인라인 3칸)
        e.add_field(name="⏳ 미참여자 (직급별)", value=" ", inline=False)
        for role_id in ROLE_ORDER:
            role = guild.get_role(role_id)
            role_name = role.name if role else str(role_id)
            e.add_field(
                name=f"◇↝{role_name}",
                value=_join_mentions_inline(non_participants_by_role.get(role_id, [])),
                inline=True,
            )

        created_utc = state.created_at.astimezone(timezone.utc)
        e.set_footer(text=f"안건 ID: {state.message_id} • 생성: {created_utc.strftime('%Y-%m-%d %H:%M UTC')}")
        return e

    @commands.hybrid_command(name="안건", description="제독/장교 회의 안건 임베드를 생성합니다.")
    async def agenda(self, ctx: commands.Context):
        if not ctx.guild:
            return
        if isinstance(ctx.interaction, discord.Interaction):
            await ctx.interaction.response.send_modal(CreateAgendaModal(self))
        else:
            await ctx.reply("이 명령은 슬래시 **/안건** 으로 사용해 주세요.", mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(AgendaCog(bot))
