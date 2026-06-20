"""
config.py – Merkezi yapılandırma modülü.
"""

import os
import logging
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise ValueError(f"Zorunlu ortam değişkeni eksik: {key}")
    return value


# ─── Finnhub ─────────────────────────────
FINNHUB_API_KEY: str = _require("FINNHUB_API_KEY")
FINNHUB_BASE_URL: str = "https://finnhub.io/api/v1"

# ─── SQLite DB ───────────────────────────
DB_PATH: str = os.getenv("DB_PATH", "bot.db")

# ─── Telegram ────────────────────────────
TELEGRAM_BOT_TOKEN: str = _require("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID: str = _require("TELEGRAM_CHAT_ID")

# ─── Uygulama ────────────────────────────
FETCH_INTERVAL_SECONDS: int = int(os.getenv("FETCH_INTERVAL_SECONDS", "300"))
MAX_NEWS_PER_FETCH: int = int(os.getenv("MAX_NEWS_PER_FETCH", "50"))
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

# ─── FinBERT ─────────────────────────────
FINBERT_MODEL: str = "ProsusAI/finbert"

IMPACT_HIGH_THRESHOLD: float = 0.80
IMPACT_MEDIUM_THRESHOLD: float = 0.55

# Gemini AI yorumlama
GEMINI_API_KEY: str | None = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_MIN_INTERVAL_SECONDS: int = int(os.getenv("GEMINI_MIN_INTERVAL_SECONDS", "60"))
GEMINI_TIMEOUT_SECONDS: int = int(os.getenv("GEMINI_TIMEOUT_SECONDS", "20"))

#FILTER SETTINGS
CONFIDENCE_LEVEL: float = float(os.getenv("CONFIDENCE_LEVEL"))

def setup_logging() -> logging.Logger:
    numeric_level = getattr(logging, LOG_LEVEL, logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger("nasdaq_bot")


logger = setup_logging()
