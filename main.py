from core.env import load_environment
from core.logging_setup import setup_logging
from core.bot import create_bot, run_bot


def main() -> None:
    setup_logging()
    load_environment()
    bot = create_bot()
    run_bot(bot)


if __name__ == "__main__":
    main()
