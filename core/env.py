import os
import re
import pathlib
import logging
from typing import Iterable

from dotenv import load_dotenv

log = logging.getLogger("SDT-BOT.env")

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"



def manual_load_env(env_path: pathlib.Path, *, override: bool = False) -> list[str]:
    """Recovers malformed .env content such as BOM, full-width equals, and special spaces."""
    try:
        raw = env_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raw = env_path.read_text(encoding="utf-16")

    def normalize(s: str) -> str:
        return (
            s.replace("\ufeff", "")
            .replace("\u200b", "")
            .replace("\u200e", "")
            .replace("\u200f", "")
            .replace("\u00a0", " ")
            .replace("＝", "=")
        )

    loaded_keys: list[str] = []

    for line in raw.splitlines():
        line = normalize(line).strip()
        if not line or line.startswith("#"):
            continue

        match = re.match(r"^\s*([^=]+?)\s*=\s*(.*?)\s*$", line)
        if not match:
            continue

        key = normalize(match.group(1)).strip()
        value = normalize(match.group(2)).strip().strip('"').strip("'")

        if not key:
            continue

        if override or key not in os.environ:
            os.environ[key] = value
            loaded_keys.append(key)

    return loaded_keys



def env_bool(key: str, default: bool = False) -> bool:
    value = os.getenv(key)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}



def env_int(key: str, default: int = 0) -> int:
    value = os.getenv(key, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default



def validate_env(required_keys: Iterable[str]) -> None:
    missing = [key for key in required_keys if not os.getenv(key)]
    if missing:
        raise RuntimeError(f"Missing required environment variable(s): {', '.join(missing)}")



def load_environment() -> None:
    if ENV_PATH.exists():
        load_dotenv(dotenv_path=ENV_PATH, override=True)
        log.info(f"📄 .env loaded (override=True): {ENV_PATH}")
    else:
        log.warning(f"📄 .env not found: {ENV_PATH}")
        return

    need_manual = any(
        not os.getenv(k)
        for k in [
            "DISCORD_TOKEN",
            "DEEPL_AUTH_KEY",
            "BATTLEPLAN_TARGET_CHANNEL_ID",
        ]
    )

    if need_manual:
        log.warning("Some ENV values are missing. Trying manual .env parsing...")
        keys = manual_load_env(ENV_PATH, override=True)
        redact = {"DISCORD_TOKEN", "DEEPL_AUTH_KEY"}
        safe_keys = [k for k in keys if k.upper() not in redact]
        log.info(f"🧾 Loaded ENV keys (redacted sensitive): {safe_keys}")

    token_state = "SET ✅" if os.getenv("DISCORD_TOKEN") else "EMPTY ❌"
    deepl_state = "SET ✅" if os.getenv("DEEPL_AUTH_KEY") else "EMPTY ❌"
    battleplan_target = os.getenv("BATTLEPLAN_TARGET_CHANNEL_ID") or "EMPTY ❌"
    auto_role_on_startup = os.getenv("AUTO_ROLE_ON_STARTUP") or "0"
    auto_role_include_bots = os.getenv("AUTO_ROLE_INCLUDE_BOTS") or "0"

    log.info(f"🔑 ENV CHECK: DISCORD_TOKEN={token_state}")
    log.info(f"🌐 ENV CHECK: DEEPL_AUTH_KEY={deepl_state}")
    log.info(f"🗺️ ENV CHECK: BATTLEPLAN_TARGET_CHANNEL_ID={battleplan_target}")
    log.info(f"🔄 ENV CHECK: AUTO_ROLE_ON_STARTUP={auto_role_on_startup}")
    log.info(f"🤖 ENV CHECK: AUTO_ROLE_INCLUDE_BOTS={auto_role_include_bots}")
