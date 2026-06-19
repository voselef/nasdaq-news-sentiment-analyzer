"""
database.py – SQLite bağlantı ve tablo yönetimi
"""

import sqlite3
import json
import logging
from contextlib import contextmanager
from typing import Generator, List, Dict

import config

logger = logging.getLogger("nasdaq_bot.database")

DB_PATH = config.DB_PATH


# ─── Bağlantı ─────────────────────────────

@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─── DDL (SQLite uyumlu) ───────────────────

_CREATE_NEWS_TABLE = """
CREATE TABLE IF NOT EXISTS news_articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id TEXT NOT NULL UNIQUE,
    headline TEXT NOT NULL,
    summary TEXT,
    source TEXT,
    url TEXT,
    published_at TEXT NOT NULL,
    sentiment TEXT,
    confidence REAL,
    impact_level TEXT,
    affected_tickers TEXT,
    ai_note TEXT,
    ai_provider TEXT,
    ai_model TEXT,
    ai_tickers TEXT,
    raw_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

_CREATE_TICKERS_TABLE = """
CREATE TABLE IF NOT EXISTS ticker_mentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    company TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

_CREATE_SEEN_ARTICLES_TABLE = """
CREATE TABLE IF NOT EXISTS seen_articles (
    article_id TEXT PRIMARY KEY,
    first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

_CREATE_BOT_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS bot_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def initialize_db() -> None:
    logger.info("SQLite veritabanı hazırlanıyor…")
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(_CREATE_NEWS_TABLE)
        cur.execute(_CREATE_TICKERS_TABLE)
        cur.execute(_CREATE_SEEN_ARTICLES_TABLE)
        cur.execute(_CREATE_BOT_STATE_TABLE)
        _ensure_news_columns(conn)
    logger.info("SQLite hazır.")


# ─── Article Exists ───────────────────────

def _ensure_news_columns(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(news_articles)")
    existing = {row["name"] for row in cur.fetchall()}
    columns = {
        "ai_note": "TEXT",
        "ai_provider": "TEXT",
        "ai_model": "TEXT",
        "ai_tickers": "TEXT",
    }
    for name, column_type in columns.items():
        if name not in existing:
            cur.execute(f"ALTER TABLE news_articles ADD COLUMN {name} {column_type}")


def article_exists(article_id: str) -> bool:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM news_articles WHERE article_id = ? LIMIT 1",
            (article_id,),
        )
        return cur.fetchone() is not None


def article_seen(article_id: str) -> bool:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM seen_articles WHERE article_id = ? LIMIT 1",
            (article_id,),
        )
        return cur.fetchone() is not None


def mark_article_seen(article_id: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO seen_articles (article_id) VALUES (?)",
            (article_id,),
        )


def mark_articles_seen(article_ids: List[str]) -> int:
    if not article_ids:
        return 0

    with get_connection() as conn:
        cur = conn.cursor()
        cur.executemany(
            "INSERT OR IGNORE INTO seen_articles (article_id) VALUES (?)",
            [(article_id,) for article_id in article_ids],
        )
        return cur.rowcount


def get_state(key: str) -> str | None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT value FROM bot_state WHERE key = ? LIMIT 1", (key,))
        row = cur.fetchone()
        return row["value"] if row else None


def set_state(key: str, value: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO bot_state (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
            """,
            (key, value),
        )


# ─── Save Article ─────────────────────────

def save_article(article: dict) -> bool:
    sql = """
        INSERT OR IGNORE INTO news_articles
        (article_id, headline, summary, source, url, published_at,
         sentiment, confidence, impact_level, affected_tickers, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    with get_connection() as conn:
        cur = conn.cursor()

        cur.execute(sql, (
            article["article_id"],
            article["headline"],
            article.get("summary"),
            article.get("source"),
            article.get("url"),
            article["published_at"],
            article.get("sentiment"),
            article.get("confidence"),
            article.get("impact_level"),
            json.dumps(article.get("affected_tickers", [])),
            json.dumps(article.get("raw_json", {})),
        ))

        inserted = cur.rowcount > 0

        if inserted and article.get("ticker_mentions"):
            cur.executemany(
                """
                INSERT INTO ticker_mentions (article_id, ticker, company)
                VALUES (?, ?, ?)
                """,
                [
                    (article["article_id"], m["ticker"], m.get("company"))
                    for m in article["ticker_mentions"]
                ],
            )

    return inserted


# ─── Recent Articles ──────────────────────

def get_recent_articles(limit: int = 20) -> List[Dict]:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT article_id, headline, sentiment, confidence,
                   impact_level, affected_tickers, published_at
            FROM news_articles
            ORDER BY published_at DESC
            LIMIT ?
        """, (limit,))

        rows = cur.fetchall()
        return [dict(row) for row in rows]


# ─── Stats ────────────────────────────────

def get_latest_article() -> Dict | None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT article_id, headline, summary, source, url, published_at,
                   sentiment, confidence, impact_level, affected_tickers, raw_json
            FROM news_articles
            ORDER BY published_at DESC, id DESC
            LIMIT 1
        """)
        row = cur.fetchone()
        if row is None:
            return None

        article = dict(row)
        cur.execute(
            """
            SELECT ticker, company
            FROM ticker_mentions
            WHERE article_id = ?
            ORDER BY id ASC
            """,
            (article["article_id"],),
        )
        article["ticker_mentions"] = [dict(ticker_row) for ticker_row in cur.fetchall()]
        return article


def get_stats() -> dict:
    with get_connection() as conn:
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM news_articles")
        total = cur.fetchone()[0]

        cur.execute("""
            SELECT sentiment, COUNT(*)
            FROM news_articles
            GROUP BY sentiment
        """)

        by_sentiment = {row[0]: row[1] for row in cur.fetchall()}

    return {
        "total_articles": total,
        "by_sentiment": by_sentiment
    }
