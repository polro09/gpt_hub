import pathlib
import logging
from typing import Iterable

log = logging.getLogger("SDT-BOT.extensions")



def discover_extensions(cogs_dir: pathlib.Path) -> list[str]:
    if not cogs_dir.exists():
        log.warning(f"📁 Cogs directory not found: {cogs_dir}")
        return []

    result: list[str] = []
    for file in sorted(cogs_dir.glob("*.py")):
        if file.name == "__init__.py":
            continue
        if file.stem.startswith("_"):
            continue
        result.append(f"cogs.{file.stem}")
    return result
