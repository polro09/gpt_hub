import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "")
WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID", "0"))
GOODBYE_CHANNEL_ID = int(os.getenv("GOODBYE_CHANNEL_ID", "0"))

WELCOME_IMAGE_URL = "https://i.imgur.com/xZCJWZR.gif"

# 첨부 이미지처럼 붉은 톤(디스코드 기본 레드 계열 느낌)
EMBED_COLOR = 0xED4245
