import logging

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



def setup_logging(level: int = logging.INFO) -> logging.Logger:
    handler = logging.StreamHandler()
    handler.setFormatter(EmojiFormatter())

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)

    return logging.getLogger("SDT-BOT")
