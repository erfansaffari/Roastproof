import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DISCORD_USER_TOKEN = os.getenv("DISCORD_USER_TOKEN", "")
RESUME_CHANNEL_ID = os.getenv("RESUME_CHANNEL_ID", "")
DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))

RESUMES_DIR = DATA_DIR / "resumes"
EXPORT_DIR = DATA_DIR / "export"
DB_PATH = DATA_DIR / "bot.db"

RATE_LIMIT_SLEEP = 0.5

for directory in (DATA_DIR, RESUMES_DIR, EXPORT_DIR):
    directory.mkdir(parents=True, exist_ok=True)
