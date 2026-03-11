import os
import re
import logging
import pathlib
import traceback

import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

# =========================
# ✅ Emoji Logger (깔끔/직관)
# =========================
LEVEL_EMOJI = {
    logging.DEBUG: "🔍",
    logging.INFO: "✅",
    logging.WARNING: "⚠️",
    logging.ERROR: "❌",
    logging.CRITICAL: "🧨",
}


class EmojiFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        emoji = LEVEL_EMOJI.get(record.levelno, "•")
        time_str = self.formatTime(record, datefmt="%H:%M:%S")
        msg = record.getMessage()
        return f"{time_str} {emoji} [{record.levelname}] {record.name} | {msg}"


handler = logging.StreamHandler()
handler.setFormatter(EmojiFormatter())

root = logging.getLogger()
root.setLevel(logging.INFO)
root.handlers.clear()
root.addHandler(handler)

log = logging.getLogger("SDT-BOT")


# =========================
# ✅ Paths
# =========================
BASE_DIR = pathlib.Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"


def manual_load_env(env_path: pathlib.Path, *, override: bool = False) -> list[str]:
    """dotenv가 못 읽는 .env(전각 등호, 특수 공백, BOM 등)까지 복구해서 로드"""
    try:
        raw = env_path.read_text(encoding="utf-8-sig")  # BOM 제거 포함
    except UnicodeDecodeError:
        raw = env_path.read_text(encoding="utf-16")

    def normalize(s: str) -> str:
        return (
            s.replace("\ufeff", "")      # BOM
            .replace("\u200b", "")      # zero-width space
            .replace("\u200e", "")      # LRM
            .replace("\u200f", "")      # RLM
            .replace("\u00a0", " ")     # NBSP
            .replace("＝", "=")         # 전각 등호
        )

    loaded_keys: list[str] = []

    for line in raw.splitlines():
        line = normalize(line).strip()
        if not line or line.startswith("#"):
            continue

        m = re.match(r"^\s*([^=]+?)\s*=\s*(.*?)\s*$", line)
        if not m:
            continue

        k = normalize(m.group(1)).strip()
        v = normalize(m.group(2)).strip().strip('"').strip("'")

        if not k:
            continue

        if override or (k not in os.environ):
            os.environ[k] = v
            loaded_keys.append(k)

    return loaded_keys


# =========================
# ✅ .env 로드
# =========================
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH, override=True)
    log.info(f"📄 .env loaded (override=True): {ENV_PATH}")
else:
    log.error(f"📄 .env NOT FOUND: {ENV_PATH}")

# 필요한 키가 비어있으면 1회만 수동 로드
if ENV_PATH.exists():
    need_manual = (
        not os.getenv("DISCORD_TOKEN")
        or not os.getenv("DEEPL_AUTH_KEY")
        or not os.getenv("BATTLEPLAN_TARGET_CHANNEL_ID")
    )

    if need_manual:
        log.warning("Some ENV values are missing. Trying manual .env parsing (override=True).")
        keys = manual_load_env(ENV_PATH, override=True)

        redact = {"DISCORD_TOKEN", "DEEPL_AUTH_KEY"}
        safe_keys = [k for k in keys if k.upper() not in redact]
        log.info(f"🧾 Loaded ENV keys (redacted sensitive): {safe_keys}")


# =========================
# ✅ ENV 체크 로그
# =========================
TOKEN = os.getenv("DISCORD_TOKEN", "")

battleplan_target = os.getenv("BATTLEPLAN_TARGET_CHANNEL_ID")
deepl_auth_set = bool(os.getenv("DEEPL_AUTH_KEY"))

auto_role_on_startup = os.getenv("AUTO_ROLE_ON_STARTUP")
auto_role_include_bots = os.getenv("AUTO_ROLE_INCLUDE_BOTS")

log.info(f"🔑 ENV CHECK: DISCORD_TOKEN={'SET ✅' if bool(TOKEN) else 'EMPTY ❌'}")
log.info(f"🌐 ENV CHECK: DEEPL_AUTH_KEY={'SET ✅' if deepl_auth_set else 'EMPTY ❌'}")
log.info(f"🗺️ ENV CHECK: BATTLEPLAN_TARGET_CHANNEL_ID={battleplan_target if battleplan_target else 'EMPTY ❌'}")

log.info(f"🔄 ENV CHECK: AUTO_ROLE_ON_STARTUP={auto_role_on_startup if auto_role_on_startup else '0'}")
log.info(f"🤖 ENV CHECK: AUTO_ROLE_INCLUDE_BOTS={auto_role_include_bots if auto_role_include_bots else '0'}")


# =========================
# ✅ Intents
# =========================
INTENTS = discord.Intents.default()
INTENTS.members = True
INTENTS.message_content = True  # Dev Portal에서도 ON 필요


class SDTBot(commands.Bot):
    async def setup_hook(self):
        extensions = [
            "cogs.welcome",
            "cogs.channel_cleanup",
            "cogs.translator_v2",
            "cogs.autorole",
            "cogs.anonymous_poll",
            "cogs.agenda",
            "cogs.resource_request",
        ]

        for ext in extensions:
            try:
                await self.load_extension(ext)
                log.info(f"🧩 Extension loaded: {ext}")
            except Exception:
                log.error(f"🧩 Extension load FAILED: {ext}\n{traceback.format_exc()}")

        # ✅ Persistent View 등록 (재시작 후에도 버튼 유지)
        # agenda / resource_request는 View가 cog 외부에서 생성 가능하므로 기존 방식 유지
        try:
            from cogs.agenda import AgendaVoteView
            self.add_view(AgendaVoteView())
            log.info("🧷 Persistent View registered: AgendaVoteView")
        except Exception:
            log.warning(f"🧷 Persistent View register skipped (AgendaVoteView)\n{traceback.format_exc()}")

        try:
            from cogs.resource_request import ResourceRequestView
            self.add_view(ResourceRequestView())
            log.info("🧷 Persistent View registered: ResourceRequestView")
        except Exception:
            log.warning(f"🧷 Persistent View register skipped (ResourceRequestView)\n{traceback.format_exc()}")

        # ✅ 참고:
        # urakan_fund는 Cog __init__ 내부에서 bot.add_view(FundView(self)) 를 수행하도록 설계되어
        # 메인에서 별도 add_view 등록이 필요하지 않습니다.

        # ✅ Slash commands sync
        try:
            synced = await self.tree.sync()
            log.info(f"🧭 Slash commands synced: {len(synced)}")
        except Exception:
            log.error(f"🧭 Slash command sync FAILED\n{traceback.format_exc()}")


def main():
    bot = SDTBot(command_prefix="!", intents=INTENTS)

    # ✅ AppCommand 에러를 SDT 로그로 100% 수집
    @bot.tree.error
    async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        cmd = getattr(interaction, "command", None)
        log.error(f"🧭 AppCommand ERROR: {cmd} | {type(error).__name__}: {error}")
        log.error(traceback.format_exc())
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Command failed. Check bot logs.", ephemeral=True)
        except Exception:
            pass

    @bot.event
    async def on_ready():
        log.info(f"🤖 Logged in: {bot.user} (ID: {bot.user.id})")

        # ✅ (안전망) guild/채널 캐시가 늦게 잡히는 경우를 대비해,
        # 자원요청 로그 View 재부착을 on_ready에서도 한번 더 시도합니다.
        try:
            cog = bot.get_cog("ResourceRequestCog")
            if cog and hasattr(cog, "_reattach_log_views"):
                await cog._reattach_log_views()
                log.info("🧷 Resource log views reattached (on_ready)")
        except Exception:
            log.warning(f"🧷 Resource log views reattach skipped (on_ready)\n{traceback.format_exc()}")

    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is empty. Check your .env.")

    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        log.error("🔐 Login failed: DISCORD_TOKEN is invalid.")
    except Exception:
        log.error(f"🧨 bot.run crashed\n{traceback.format_exc()}")


if __name__ == "__main__":
    main()
