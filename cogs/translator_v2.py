import os
import re
import json
import asyncio
import pathlib
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

import discord
from discord.ext import commands
from discord import app_commands

import deepl

log = logging.getLogger("SDT-BOT.translator")


# =========================================================
# Language detection (KO / EN / JA / RU)
# =========================================================
HANGUL_RE = re.compile(r"[가-힣]")
LATIN_RE = re.compile(r"[A-Za-z]")
JAPANESE_RE = re.compile(r"[ぁ-ゟァ-ヿ一-龯]")
CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")

URL_RE = re.compile(r"(https?://\S+|www\.\S+)", re.IGNORECASE)
CUSTOM_EMOJI_RE = re.compile(r"<a?:[A-Za-z0-9_~\-]+:\d+>")
MENTION_RE = re.compile(r"<@[!&]?\d+>|<#\d+>")
CODEBLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`]*`")
DISCORD_SNOWFLAKE_ONLY_RE = re.compile(r"^\d{15,22}$")
MULTISPACE_RE = re.compile(r"\s+")

# 아주 짧은 반응성 영어는 번역 제외
SHORT_ENGLISH_REACTIONS = {
    "lol", "lmao", "rofl", "ok", "k", "kk", "thx", "ty", "np", "gn", "gm",
    "hi", "yo", "bro", "gg", "wp", "ez", "omg", "wtf", "fr", "ngl", "idk",
    "brb", "afk", "rip", "yep", "nope", "nice", "wow", "haha", "xd", "oof"
}


def detect_lang(text: str) -> Optional[str]:
    if not text:
        return None

    hangul = len(HANGUL_RE.findall(text))
    latin = len(LATIN_RE.findall(text))
    jp = len(JAPANESE_RE.findall(text))
    ru = len(CYRILLIC_RE.findall(text))

    if hangul == 0 and latin == 0 and jp == 0 and ru == 0:
        return None

    m = max(hangul, latin, jp, ru)
    if m == hangul:
        return "KO"
    if m == jp:
        return "JA"
    if m == ru:
        return "RU"
    return "EN"


def normalize_lang_code(code: str) -> Optional[str]:
    """
    사용자 입력용 코드 정규화
    ko, kr -> KO
    en -> EN
    ja, jp -> JA
    ru -> RU
    """
    if not code:
        return None

    c = code.strip().lower()
    mapping = {
        "ko": "KO",
        "kr": "KO",
        "en": "EN",
        "en-us": "EN",
        "us": "EN",
        "ja": "JA",
        "jp": "JA",
        "ru": "RU",
    }
    return mapping.get(c)


def ui_lang(code: str) -> str:
    return "JP" if code == "JA" else code


def flag_of(code: str) -> str:
    return {
        "KO": "🇰🇷",
        "EN": "🇺🇸",
        "JA": "🇯🇵",
        "RU": "🇷🇺",
    }.get(code, "🏳️")


def make_pair_key(src: str, tgt: str) -> str:
    return f"{src}2{tgt}"


def now_ts() -> float:
    return discord.utils.utcnow().timestamp()


def utc_stamp() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


def strip_for_translation(text: str) -> str:
    """
    번역 전에 불필요 요소 제거:
    - URL
    - 디스코드 커스텀 이모지
    - 멘션 / 채널멘션 / 역할멘션
    - 코드블럭 / 인라인코드
    """
    if not text:
        return ""

    x = text
    x = CODEBLOCK_RE.sub(" ", x)
    x = INLINE_CODE_RE.sub(" ", x)
    x = URL_RE.sub(" ", x)
    x = CUSTOM_EMOJI_RE.sub(" ", x)
    x = MENTION_RE.sub(" ", x)

    # 일반적인 디스코드 포맷 잔여물 약간 정리
    x = x.replace("**", " ").replace("__", " ").replace("~~", " ").replace("||", " ")
    x = MULTISPACE_RE.sub(" ", x).strip()
    return x


def is_meaningful_for_translation(cleaned: str) -> bool:
    if not cleaned:
        return False

    if DISCORD_SNOWFLAKE_ONLY_RE.fullmatch(cleaned):
        return False

    lang = detect_lang(cleaned)
    if lang is None:
        return False

    # 짧은 영어 반응은 번역하지 않음
    if lang == "EN":
        low = cleaned.lower().strip(" .,!?:;~_-")
        if low in SHORT_ENGLISH_REACTIONS:
            return False

        only_words = re.findall(r"[A-Za-z]+", low)
        if len(only_words) == 1 and len(only_words[0]) <= 3:
            return False

    # 글자 수가 너무 짧고 정보성이 약하면 스킵
    letters = re.findall(r"[A-Za-z가-힣ぁ-ゟァ-ヿ一-龯А-Яа-яЁё]", cleaned)
    if len(letters) <= 1:
        return False

    return True


# =========================================================
# Pair definitions
# =========================================================
LANGS = ["EN", "KO", "JA", "RU"]

PAIR_DEFS: Dict[str, Dict[str, str]] = {}
for src in LANGS:
    for tgt in LANGS:
        if src == tgt:
            continue
        key = make_pair_key(src, tgt)
        PAIR_DEFS[key] = {
            "src": src,
            "tgt": "EN-US" if tgt == "EN" else tgt,
            "ui_src": src,
            "ui_tgt": tgt,
            "label": f"{flag_of(src)} {ui_lang(src)} → {flag_of(tgt)} {ui_lang(tgt)}",
        }

# 언어별 묶음 정렬
PAIR_ORDER = [
    # EN group
    "EN2KO", "EN2JA", "EN2RU",
    # KO group
    "KO2EN", "KO2JA", "KO2RU",
    # JA group
    "JA2KO", "JA2EN", "JA2RU",
    # RU group
    "RU2KO", "RU2EN", "RU2JA",
]


# =========================================================
# Storage
# - 기존 포맷 {"pairs":[...]} 호환 유지
# - 신규 포맷 {"pairs":[...], "relay_channel_id": 123}
# =========================================================
BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = DATA_DIR / "translator_config.json"
LOG_DIR = DATA_DIR / "translator_logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


class ConfigStore:
    def __init__(self, path: pathlib.Path):
        self.path = path
        self.lock = asyncio.Lock()
        self.data: Dict[str, Any] = {"guilds": {}}

    async def _write_json(self, obj: Dict[str, Any]):
        text = json.dumps(obj, ensure_ascii=False, indent=2)

        def _w():
            self.path.write_text(text, encoding="utf-8")

        await asyncio.to_thread(_w)

    async def _read_text(self) -> str:
        def _r():
            return self.path.read_text(encoding="utf-8")
        return await asyncio.to_thread(_r)

    def _normalize_channel_obj(self, raw: Any) -> Dict[str, Any]:
        """
        구버전 호환:
        - {"pairs":[...]}              -> OK
        - {"pairs":[...], relay...}   -> OK
        - ["EN2KO", ...]              -> 구형 이상 포맷도 복구
        """
        if isinstance(raw, dict):
            pairs = raw.get("pairs", [])
            relay_channel_id = raw.get("relay_channel_id")
        elif isinstance(raw, list):
            pairs = raw
            relay_channel_id = None
        else:
            pairs = []
            relay_channel_id = None

        if not isinstance(pairs, list):
            pairs = []

        pairs = [p for p in pairs if p in PAIR_DEFS]

        if relay_channel_id is not None:
            try:
                relay_channel_id = int(relay_channel_id)
            except Exception:
                relay_channel_id = None

        return {
            "pairs": pairs,
            "relay_channel_id": relay_channel_id,
        }

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
                await self._write_json(self.data)

            # 로드 시점에 구 설정 자동 보정
            changed = False
            guilds = self.data.setdefault("guilds", {})
            for gid, g in guilds.items():
                if not isinstance(g, dict):
                    guilds[gid] = {"enabled": False, "channels": {}}
                    changed = True
                    continue

                g.setdefault("enabled", False)
                chans = g.setdefault("channels", {})
                if not isinstance(chans, dict):
                    g["channels"] = {}
                    changed = True
                    continue

                for cid, chobj in list(chans.items()):
                    norm = self._normalize_channel_obj(chobj)
                    if chobj != norm:
                        chans[cid] = norm
                        changed = True

            if changed:
                await self._write_json(self.data)

    async def _save_unlocked(self):
        await self._write_json(self.data)

    def _g_unlocked(self, guild_id: int) -> Dict[str, Any]:
        g = self.data.setdefault("guilds", {}).setdefault(str(guild_id), {})
        g.setdefault("enabled", False)
        g.setdefault("channels", {})
        return g

    def _get_channel_obj_unlocked(self, guild_id: int, channel_id: int) -> Dict[str, Any]:
        g = self._g_unlocked(guild_id)
        chans = g.setdefault("channels", {})
        raw = chans.setdefault(str(channel_id), {"pairs": [], "relay_channel_id": None})
        norm = self._normalize_channel_obj(raw)
        chans[str(channel_id)] = norm
        return norm

    async def set_guild_enabled(self, guild_id: int, enabled: bool):
        async with self.lock:
            self._g_unlocked(guild_id)["enabled"] = enabled
            await self._save_unlocked()

    async def is_guild_enabled(self, guild_id: int) -> bool:
        async with self.lock:
            return bool(self._g_unlocked(guild_id).get("enabled", False))

    async def set_channel_pairs(self, guild_id: int, channel_id: int, pairs: List[str]):
        pairs = [p for p in pairs if p in PAIR_DEFS]
        async with self.lock:
            ch = self._get_channel_obj_unlocked(guild_id, channel_id)
            ch["pairs"] = pairs
            await self._save_unlocked()

    async def set_channel_relay(self, guild_id: int, channel_id: int, relay_channel_id: Optional[int]):
        async with self.lock:
            ch = self._get_channel_obj_unlocked(guild_id, channel_id)
            ch["relay_channel_id"] = int(relay_channel_id) if relay_channel_id else None
            await self._save_unlocked()

    async def get_channel_config(self, guild_id: int, channel_id: int) -> Dict[str, Any]:
        async with self.lock:
            ch = self._get_channel_obj_unlocked(guild_id, channel_id)
            return {
                "pairs": list(ch.get("pairs", [])),
                "relay_channel_id": ch.get("relay_channel_id"),
            }

    async def get_channel_pairs(self, guild_id: int, channel_id: int) -> List[str]:
        cfg = await self.get_channel_config(guild_id, channel_id)
        return list(cfg.get("pairs", []))

    async def clear_channel(self, guild_id: int, channel_id: int):
        async with self.lock:
            g = self._g_unlocked(guild_id)
            g.get("channels", {}).pop(str(channel_id), None)
            await self._save_unlocked()

    async def list_channels(self, guild_id: int) -> Dict[int, Dict[str, Any]]:
        async with self.lock:
            g = self._g_unlocked(guild_id)
            out: Dict[int, Dict[str, Any]] = {}
            for k, v in g.get("channels", {}).items():
                try:
                    cid = int(k)
                except Exception:
                    continue
                norm = self._normalize_channel_obj(v)
                out[cid] = {
                    "pairs": list(norm.get("pairs", [])),
                    "relay_channel_id": norm.get("relay_channel_id"),
                }
            return out


# =========================================================
# UI
# =========================================================
class PairSelect(discord.ui.Select):
    def __init__(self, current: List[str]):
        options = []
        current_set = set(current)

        for key in PAIR_ORDER:
            meta = PAIR_DEFS[key]
            options.append(
                discord.SelectOption(
                    label=meta["label"],
                    value=key,
                    description=f"{ui_lang(meta['ui_src'])} source group",
                    default=(key in current_set),
                )
            )

        super().__init__(
            placeholder="Select translation directions (multi-select)",
            min_values=0,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)


class ChannelSetupView(discord.ui.View):
    def __init__(self, cog: "TranslatorV2", guild_id: int, channel_id: int, current: List[str]):
        super().__init__(timeout=180)
        self.cog = cog
        self.guild_id = guild_id
        self.channel_id = channel_id

        self.select = PairSelect(current)
        self.add_item(self.select)

    def pretty(self, pairs: List[str]) -> str:
        if not pairs:
            return "❌ No directions selected"

        grouped: Dict[str, List[str]] = {"EN": [], "KO": [], "JA": [], "RU": []}
        for p in pairs:
            src = PAIR_DEFS[p]["ui_src"]
            grouped.setdefault(src, []).append(PAIR_DEFS[p]["label"])

        chunks = []
        for src in ["EN", "KO", "JA", "RU"]:
            labels = grouped.get(src, [])
            if labels:
                chunks.append(f"**{flag_of(src)} {ui_lang(src)} source**\n" + "\n".join(f"✅ {x}" for x in labels))
        return "\n\n".join(chunks)

    @discord.ui.button(label="Save", style=discord.ButtonStyle.success, emoji="💾")
    async def save_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        selected = list(getattr(self.select, "values", []))
        await self.cog.store.set_channel_pairs(self.guild_id, self.channel_id, selected)

        cfg = await self.cog.store.get_channel_config(self.guild_id, self.channel_id)
        relay = cfg.get("relay_channel_id")

        relay_text = f"\n\n📤 Output Channel: <#{relay}>" if relay else "\n\n📤 Output Channel: same as source"
        e = discord.Embed(
            title="💾 Saved!",
            description=f"Source Channel: <#{self.channel_id}>{relay_text}\n\n{self.pretty(selected)}",
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(embed=e, view=None)

    @discord.ui.button(label="Clear", style=discord.ButtonStyle.danger, emoji="🧹")
    async def clear_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.store.clear_channel(self.guild_id, self.channel_id)
        e = discord.Embed(
            title="🧹 Cleared",
            description=f"Channel: <#{self.channel_id}>\n\n✅ This channel no longer has translation enabled.",
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(embed=e, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        e = discord.Embed(
            title="✖️ Cancelled",
            description="No changes were made.",
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=e, view=None)


# =========================================================
# COG
# =========================================================
class TranslatorV2(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = ConfigStore(CONFIG_PATH)

        auth_key = os.getenv("DEEPL_AUTH_KEY")
        self.translator: Optional[deepl.Translator] = deepl.Translator(auth_key) if auth_key else None
        if self.translator is None:
            log.warning("DEEPL_AUTH_KEY is missing. Translator will load but translation will not run.")

        self._last_sent: Dict[int, float] = {}
        self._last_manual_sent: Dict[int, float] = {}

        self.translator_group = app_commands.Group(
            name="translator",
            description="Server/channel translator settings (DeepL)",
        )
        self._register_app_commands()

    async def cog_load(self):
        await self.store.load()

    def _admin_only(self, interaction: discord.Interaction) -> bool:
        perms = interaction.user.guild_permissions  # type: ignore
        return bool(perms.administrator or perms.manage_guild)

    async def _update_log_latest_first(self, guild_id: int, channel_id: int, new_block: str, *, max_chars: int = 200_000):
        gdir = LOG_DIR / str(guild_id)
        gdir.mkdir(parents=True, exist_ok=True)
        path = gdir / f"{channel_id}.txt"

        def rw():
            old = ""
            if path.exists():
                try:
                    old = path.read_text(encoding="utf-8")
                except Exception:
                    old = ""
            combined = new_block + old
            if len(combined) > max_chars:
                combined = combined[:max_chars]
            path.write_text(combined, encoding="utf-8")

        await asyncio.to_thread(rw)

    def _build_embed(
        self,
        author: discord.abc.User,
        translations: List[Tuple[str, str]],
        *,
        source_channel_id: Optional[int] = None,
        manual: bool = False,
        cleaned_original: Optional[str] = None,
    ) -> discord.Embed:
        display_name = getattr(author, "display_name", str(author))
        title = f"🗣️ {display_name}"
        if manual:
            title = f"🗣️ {display_name} • Manual Translate"

        e = discord.Embed(
            title=title,
            description="",
            color=discord.Color.blurple(),
        )

        if source_channel_id:
            e.add_field(name="📍 Source Channel", value=f"<#{source_channel_id}>", inline=False)

        if cleaned_original:
            shown = cleaned_original[:900]
            e.add_field(name="📝 Source Text", value=f"```{shown}```", inline=False)

        for label, text in translations:
            e.add_field(name=label, value=f"```{text[:900]}```", inline=False)

        e.set_footer(text="DeepL API • Multi-direction channel translator")

        try:
            e.set_thumbnail(url=author.display_avatar.url)  # type: ignore
        except Exception:
            pass

        return e

    def _pair_label(self, src: str, tgt: str) -> str:
        return f"{flag_of(src)} {ui_lang(src)} → {flag_of(tgt)} {ui_lang(tgt)}"

    async def _translate_with_pair(self, text: str, src: str, tgt: str) -> Optional[Tuple[str, str]]:
        if self.translator is None:
            return None

        deepl_target = "EN-US" if tgt == "EN" else tgt
        label = self._pair_label(src, tgt)

        try:
            res = await asyncio.to_thread(
                self.translator.translate_text,
                text,
                source_lang=src,
                target_lang=deepl_target,
            )
            return (label, str(res))
        except Exception as e:
            log.warning(f"DeepL translate failed for {src}->{tgt}: {type(e).__name__}: {e}")
            return None

    async def _translate_multi(self, text: str, enabled_pairs: List[str]) -> Tuple[str, List[Tuple[str, str]]]:
        """
        반환값:
        - cleaned_text
        - translations
        """
        if self.translator is None:
            return "", []

        cleaned = strip_for_translation(text)
        if not is_meaningful_for_translation(cleaned):
            return cleaned, []

        lang = detect_lang(cleaned)
        if lang is None:
            return cleaned, []

        pairs = [p for p in enabled_pairs if PAIR_DEFS[p]["src"] == lang]
        if not pairs:
            return cleaned, []

        async def run_one(p: str) -> Optional[Tuple[str, str]]:
            src = PAIR_DEFS[p]["ui_src"]
            tgt = PAIR_DEFS[p]["ui_tgt"]
            return await self._translate_with_pair(cleaned, src, tgt)

        results = await asyncio.gather(*(run_one(p) for p in pairs))
        return cleaned, [r for r in results if r is not None]

    async def _send_translation_result(
        self,
        *,
        message: discord.Message,
        cleaned_original: str,
        translations: List[Tuple[str, str]],
        relay_channel_id: Optional[int] = None,
        manual: bool = False,
    ):
        if not translations:
            return

        target_channel: discord.abc.MessageableChannel = message.channel

        if relay_channel_id and message.guild:
            relay_channel = message.guild.get_channel(relay_channel_id)
            if relay_channel is not None:
                target_channel = relay_channel

        embed = self._build_embed(
            message.author,
            translations,
            source_channel_id=message.channel.id if relay_channel_id else None,
            manual=manual,
            cleaned_original=cleaned_original if manual else None,
        )

        try:
            if target_channel.id == message.channel.id:  # type: ignore
                await target_channel.send(embed=embed, reference=message, mention_author=False)  # type: ignore
            else:
                await target_channel.send(embed=embed)  # type: ignore
        except discord.HTTPException:
            await target_channel.send(embed=embed)  # type: ignore

    async def _handle_manual_translate_command(self, message: discord.Message) -> bool:
        """
        !t ko en 안녕하세요
        !t ja ko こんにちは
        """
        content = message.content.strip()
        m = re.match(r"^!t\s+([A-Za-z\-]+)\s+([A-Za-z\-]+)\s+(.+)$", content, re.DOTALL)
        if not m:
            return False

        if message.guild is None or message.author.bot:
            return True

        if self.translator is None:
            try:
                await message.reply("❌ DEEPL_AUTH_KEY is missing.", mention_author=False)
            except Exception:
                pass
            return True

        src_raw, tgt_raw, raw_text = m.group(1), m.group(2), m.group(3).strip()
        src = normalize_lang_code(src_raw)
        tgt = normalize_lang_code(tgt_raw)

        # 명령 메시지 삭제
        try:
            await message.delete()
        except Exception:
            pass

        if src is None or tgt is None:
            try:
                await message.channel.send(
                    "❌ Usage: `!t ko en 내용` / supported: `ko, en, ja(jp), ru`"
                )
            except Exception:
                pass
            return True

        if src == tgt:
            try:
                await message.channel.send("❌ Source and target languages cannot be the same.")
            except Exception:
                pass
            return True

        cleaned = strip_for_translation(raw_text)
        if not is_meaningful_for_translation(cleaned):
            return True

        ch_id = message.channel.id
        last = self._last_manual_sent.get(ch_id, 0.0)
        if now_ts() - last < 0.5:
            return True

        self._last_manual_sent[ch_id] = now_ts()

        result = await self._translate_with_pair(cleaned, src, tgt)
        if not result:
            return True

        await self._send_translation_result(
            message=message,
            cleaned_original=cleaned,
            translations=[result],
            relay_channel_id=None,
            manual=True,
        )
        return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return

        if not message.content or not message.content.strip():
            return

        # 수동 !t 명령은 설정 채널이 아니어도 작동
        if message.content.startswith("!t "):
            handled = await self._handle_manual_translate_command(message)
            if handled:
                return

        # 다른 일반 명령어는 자동 번역 제외
        if message.content.startswith(("!", "?", ".", "/")):
            return

        if self.translator is None:
            return

        guild_id = message.guild.id
        channel_id = message.channel.id

        if not await self.store.is_guild_enabled(guild_id):
            return

        cfg = await self.store.get_channel_config(guild_id, channel_id)
        pairs = cfg.get("pairs", [])
        relay_channel_id = cfg.get("relay_channel_id")

        if not pairs:
            return

        now = now_ts()
        last = self._last_sent.get(channel_id, 0.0)
        if now - last < 0.6:
            return

        cleaned, translations = await self._translate_multi(message.content, pairs)
        if not translations:
            return

        self._last_sent[channel_id] = now

        await self._send_translation_result(
            message=message,
            cleaned_original=cleaned,
            translations=translations,
            relay_channel_id=relay_channel_id,
            manual=False,
        )

        # 로그 저장
        lines = [
            "\n" + "=" * 70 + "\n",
            f"[{utc_stamp()}]\n",
            f"Author: {message.author} ({message.author.id})\n",
            f"Source Channel: {message.channel.id}\n",
            f"Relay Channel: {relay_channel_id if relay_channel_id else message.channel.id}\n",
            "Original Raw:\n",
            message.content + "\n\n",
            "Original Cleaned:\n",
            cleaned + "\n\n",
            "Translations:\n",
        ]
        for label, text_out in translations:
            lines.append(f"- {label}\n{text_out}\n\n")

        await self._update_log_latest_first(guild_id, channel_id, "".join(lines))

    # =========================================================
    # Slash Commands
    # =========================================================
    def _register_app_commands(self):
        @self.translator_group.command(name="server", description="Enable/disable this server as a translation server.")
        @app_commands.describe(mode="Choose enable or disable")
        @app_commands.choices(mode=[
            app_commands.Choice(name="enable", value="enable"),
            app_commands.Choice(name="disable", value="disable"),
        ])
        async def _server(interaction: discord.Interaction, mode: app_commands.Choice[str]):
            await interaction.response.defer(ephemeral=True)

            if interaction.guild is None:
                return await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)
            if not self._admin_only(interaction):
                return await interaction.followup.send("⛔ Admin only (Manage Server / Administrator).", ephemeral=True)

            enabled = (mode.value == "enable")
            await self.store.set_guild_enabled(interaction.guild.id, enabled)

            e = discord.Embed(
                title="🛠️ Translator Server Setting",
                description=(
                    "✅ Enabled. This server is now a translation server."
                    if enabled else
                    "🛑 Disabled. Translator is off for this server."
                ),
                color=discord.Color.green() if enabled else discord.Color.red(),
            )
            await interaction.followup.send(embed=e, ephemeral=True)

        @self.translator_group.command(name="channel", description="Configure translation directions for this source channel.")
        @app_commands.describe(
            channel="Source channel to configure. Leave empty for current channel."
        )
        async def _channel(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
            await interaction.response.defer(ephemeral=True)

            if interaction.guild is None or interaction.channel is None:
                return await interaction.followup.send("❌ Use this in a server channel.", ephemeral=True)
            if not self._admin_only(interaction):
                return await interaction.followup.send("⛔ Admin only (Manage Server / Administrator).", ephemeral=True)

            if not await self.store.is_guild_enabled(interaction.guild.id):
                e = discord.Embed(
                    title="⚠️ Translator is disabled",
                    description="➡️ Use **/translator server enable** first.",
                    color=discord.Color.orange(),
                )
                return await interaction.followup.send(embed=e, ephemeral=True)

            target_channel = channel or interaction.channel
            cid = target_channel.id
            cfg = await self.store.get_channel_config(interaction.guild.id, cid)
            current = cfg.get("pairs", [])
            relay = cfg.get("relay_channel_id")

            e = discord.Embed(
                title="⚙️ Channel Translator Setup",
                description=f"Source Channel: <#{cid}>\n\nSelect one or more translation directions.",
                color=discord.Color.blurple(),
            )
            e.add_field(
                name="📌 Current Selection",
                value=("\n".join(f"✅ {PAIR_DEFS[p]['label']}" for p in current)) if current else "❌ None",
                inline=False,
            )
            e.add_field(
                name="📤 Output Channel",
                value=f"<#{relay}>" if relay else "same as source",
                inline=False,
            )

            view = ChannelSetupView(self, interaction.guild.id, cid, current)
            await interaction.followup.send(embed=e, view=view, ephemeral=True)

        @self.translator_group.command(name="route", description="Set where translated messages from source channel will be sent.")
        @app_commands.describe(
            source_channel="Channel where original messages are read",
            output_channel="Channel where translated embeds are sent. Set same as source if you want same-channel output",
        )
        async def _route(
            interaction: discord.Interaction,
            source_channel: discord.TextChannel,
            output_channel: discord.TextChannel,
        ):
            await interaction.response.defer(ephemeral=True)

            if interaction.guild is None:
                return await interaction.followup.send("❌ Use this in a server.", ephemeral=True)
            if not self._admin_only(interaction):
                return await interaction.followup.send("⛔ Admin only (Manage Server / Administrator).", ephemeral=True)

            # source와 output이 같으면 same-channel 동작으로 간주해 relay 저장 제거
            relay_channel_id = None if source_channel.id == output_channel.id else output_channel.id
            await self.store.set_channel_relay(interaction.guild.id, source_channel.id, relay_channel_id)

            e = discord.Embed(
                title="📤 Translation Route Saved",
                description=(
                    f"Source: <#{source_channel.id}>\n"
                    f"Output: {'same as source' if relay_channel_id is None else f'<#{relay_channel_id}>'}"
                ),
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=e, ephemeral=True)

        @self.translator_group.command(name="clear_route", description="Clear relay route and send translated messages back to the same source channel.")
        @app_commands.describe(source_channel="Configured source channel")
        async def _clear_route(interaction: discord.Interaction, source_channel: discord.TextChannel):
            await interaction.response.defer(ephemeral=True)

            if interaction.guild is None:
                return await interaction.followup.send("❌ Use this in a server.", ephemeral=True)
            if not self._admin_only(interaction):
                return await interaction.followup.send("⛔ Admin only (Manage Server / Administrator).", ephemeral=True)

            await self.store.set_channel_relay(interaction.guild.id, source_channel.id, None)

            e = discord.Embed(
                title="🧹 Route Cleared",
                description=f"Source: <#{source_channel.id}>\nOutput: same as source",
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=e, ephemeral=True)

        @self.translator_group.command(name="status", description="Show enabled channels, directions, and relay routes in this server.")
        async def _status(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)

            if interaction.guild is None:
                return await interaction.followup.send("❌ Use this in a server.", ephemeral=True)

            enabled = await self.store.is_guild_enabled(interaction.guild.id)
            channels = await self.store.list_channels(interaction.guild.id)

            e = discord.Embed(
                title="📡 Translator Status",
                description=f"{'✅ Enabled' if enabled else '🛑 Disabled'}",
                color=discord.Color.green() if enabled else discord.Color.red(),
            )

            if not channels:
                e.add_field(name="Channels", value="No channels configured.", inline=False)
            else:
                lines = []
                for cid2, cfg in sorted(channels.items(), key=lambda x: x[0]):
                    pairs2 = cfg.get("pairs", [])
                    relay = cfg.get("relay_channel_id")

                    if not pairs2:
                        continue

                    pretty = ", ".join(PAIR_DEFS[p]["label"] for p in pairs2)
                    route = f"<#{relay}>" if relay else "same"
                    lines.append(f"• <#{cid2}> → [{route}]\n  {pretty}")

                e.add_field(
                    name="Configured Channels",
                    value="\n".join(lines)[:1024] if lines else "No channels configured.",
                    inline=False
                )

            e.add_field(
                name="Manual Command",
                value="`!t ko en 안녕하세요`\n`!t en ru hello world`\nWorks even outside configured channels.",
                inline=False,
            )

            e.set_footer(text="DeepL API • Multi-direction channel translator")
            await interaction.followup.send(embed=e, ephemeral=True)


async def setup(bot: commands.Bot):
    cog = TranslatorV2(bot)
    await bot.add_cog(cog)

    try:
        bot.tree.add_command(cog.translator_group)
    except app_commands.CommandAlreadyRegistered:
        pass

    log.info("cogs.translator_v2 loaded")