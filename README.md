# 📈 NASDAQ News Analysis & Trade Signal Bot

A production-grade NASDAQ news analysis and trade signal system integrated with FinBERT + spaCy + Finnhub + PostgreSQL + Telegram.

---

## 🏗 Architecture

    Finnhub API
        │
        ▼
    FinnhubClient          ← News fetching & normalization
        │
        ▼
    EntityExtractor        ← spaCy NER → Company/Ticker detection
        │
        ▼
    SentimentAnalyzer      ← FinBERT → positive/negative/neutral + confidence score
        │
        ▼
    TradeSignalEngine      ← Decision matrix → BUY/SELL/HOLD/WATCH + risk level
        │
        ├──▶ PostgreSQL    ← Duplicate prevention + persistent storage
        │
        └──▶ TelegramBot   ← Formatted notification + trade signal

---

## 📁 Project Structure

    nasdaq_bot/
    ├── main.py              # Main orchestration engine & CLI
    ├── config.py            # Central configuration (.env reader)
    ├── database.py          # PostgreSQL connection + schema + CRUD
    ├── finnhub_client.py    # Finnhub REST API client
    ├── entity_extractor.py  # spaCy NER + NASDAQ ticker dictionary
    ├── sentiment.py         # FinBERT sentiment analysis
    ├── trade_signal.py      # Trade signal engine (BUY/SELL/HOLD/WATCH)
    ├── telegram_bot.py      # Telegram Bot API client
    ├── requirements.txt
    ├── .env.example
    └── README.md

---

## ⚙️ Installation

### 1. Prerequisites

* Python 3.12+
* PostgreSQL 14+
* Finnhub API Key → https://finnhub.io
* Telegram Bot Token → @BotFather
* Telegram Chat ID → @userinfobot

### 2. Environment Setup

    git clone <repo_url>
    cd nasdaq_bot

    python -m venv .venv
    source .venv/bin/activate        # Windows: .venv\Scripts\activate

    pip install -r requirements.txt

    # spaCy English model (required)
    python -m spacy download en_core_web_sm

### 3. Environment Variables

    cp .env .env
    # Edit the .env file:

    FINNHUB_API_KEY=d1abc...
    DB_HOST=localhost
    DB_PORT=5432
    DB_NAME=nasdaq_news
    DB_USER=postgres
    DB_PASSWORD=secret
    TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
    TELEGRAM_CHAT_ID=-100123456789
    FETCH_INTERVAL_SECONDS=300
    LOG_LEVEL=INFO
    MAX_NEWS_PER_FETCH=50

### 4. PostgreSQL Database

    CREATE DATABASE nasdaq_news;

### 5. Setup DB Schema

    python main.py --init-db

---

## 🚀 Execution

| Command | Description |
| :--- | :--- |
| `python main.py` | Continuous loop (default, at `FETCH_INTERVAL_SECONDS` interval) |
| `python main.py --once` | Run once (cron compatible) |
| `python main.py --init-db` | Only setup the DB schema |
| `python main.py --stats` | Print DB statistics to the screen |
| `python main.py --summary` | Send a daily summary to Telegram |
| `python main.py --test-telegram` | Test the Telegram connection |

### Cron Example (every 5 minutes)

    */5 * * * * /path/to/.venv/bin/python /path/to/nasdaq_bot/main.py --once >> /var/log/nasdaq_bot.log 2>&1

### Systemd Service (continuous loop)

    [Unit]
    Description=NASDAQ Trade Bot
    After=network.target postgresql.service

    [Service]
    Type=simple
    User=ubuntu
    WorkingDirectory=/opt/nasdaq_bot
    ExecStart=/opt/nasdaq_bot/.venv/bin/python main.py
    Restart=on-failure
    RestartSec=30

    [Install]
    WantedBy=multi-user.target

---

## 📊 Database Schema

### `news_articles`

| Column | Type | Description |
| :--- | :--- | :--- |
| `article_id` | TEXT UNIQUE | Finnhub unique ID |
| `headline` | TEXT | News headline |
| `summary` | TEXT | Summary |
| `source` | TEXT | News source |
| `url` | TEXT | Link |
| `published_at` | TIMESTAMPTZ | Publish date |
| `sentiment` | TEXT | positive/negative/neutral |
| `confidence` | NUMERIC | 0.0–1.0 |
| `impact_level` | TEXT | LOW/MEDIUM/HIGH |
| `affected_tickers` | TEXT[] | NVDA, AMD… |
| `raw_json` | JSONB | Original API response |

### `ticker_mentions`

| Column | Type | Description |
| :--- | :--- | :--- |
| `article_id` | TEXT | FK → news_articles |
| `ticker` | TEXT | NVDA |
| `company` | TEXT | Nvidia |

---

## 📱 Telegram Output Example

    🚨 NASDAQ NEWS
    ━━━━━━━━━━━━━━━━━━━━━━━━━
    📰 Headline: Nvidia Posts Record Q2 Revenue, Beats Estimates
    🏢 Companies: NVDA, AMD, INTC
    🟢 Sentiment: Positive
    🎯 Confidence Score: 91%
    📈 Impact Level: HIGH
    📡 Source: Reuters
    🕐 Date: 2024-08-28 18:34 UTC

    ━━━━━━━━━━━━━━━━━━━━━━━━━
    📊 TRADE SIGNAL
    ⚡ Action: 🟢 BUY
    🎯 Symbols: NVDA, AMD, INTC
    🎲 Confidence: 91%
    🔵 Risk: LOW
    💡 Reasoning: Positive news flow detected for NVDA, AMD, INTC
       (91% confidence). High impact level — short/medium-term BUY
       opportunity can be considered.

---

## 🧪 Testing

    # Telegram connection test
    python main.py --test-telegram

    # Single loop (DB + Telegram)
    python main.py --once

    # Statistics
    python main.py --stats

---

## 🔧 Development

### Customizing Trade Signal Logic

The strategy can be easily adapted by modifying `_DECISION_MATRIX` inside `trade_signal.py`:

    _DECISION_MATRIX = {
        ("positive", "HIGH"):   ("BUY",   "LOW"),
        ("negative", "HIGH"):   ("SELL",  "HIGH"),
        ...
    }

### Adding a New Ticker

Add it to the `TICKER_MAP` dictionary in `entity_extractor.py`:

    "your company": "TICK",

---

## ⚠️ Disclaimer

This bot is for **research and informational purposes only**.
The generated trade signals **do not constitute financial advice**.
Please consult a financial advisor before using it with real money.
