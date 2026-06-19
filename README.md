# 📈 NASDAQ Haber Analiz & Trade Sinyal Botu

FinBERT + spaCy + Finnhub + PostgreSQL + Telegram entegrasyonlu,
production-grade NASDAQ haber analiz ve trade sinyal sistemi.

---

## 🏗 Mimari

```
Finnhub API
    │
    ▼
FinnhubClient          ← Haber çekme & normalleştirme
    │
    ▼
EntityExtractor        ← spaCy NER → Şirket/Ticker tespiti
    │
    ▼
SentimentAnalyzer      ← FinBERT → positive/negative/neutral + güven skoru
    │
    ▼
TradeSignalEngine      ← Karar matrisi → BUY/SELL/HOLD/WATCH + risk seviyesi
    │
    ├──▶ PostgreSQL     ← Duplicate engelleme + kalıcı kayıt
    │
    └──▶ TelegramBot    ← Biçimlendirilmiş bildirim + trade sinyali
```

---

## 📁 Proje Yapısı

```
nasdaq_bot/
├── main.py              # Ana orkestrasyon motoru & CLI
├── config.py            # Merkezi yapılandırma (.env okuyucu)
├── database.py          # PostgreSQL bağlantı + şema + CRUD
├── finnhub_client.py    # Finnhub REST API istemcisi
├── entity_extractor.py  # spaCy NER + NASDAQ ticker sözlüğü
├── sentiment.py         # FinBERT duygu analizi
├── trade_signal.py      # Trade sinyal motoru (BUY/SELL/HOLD/WATCH)
├── telegram_bot.py      # Telegram Bot API istemcisi
├── requirements.txt
├── .env.example
└── README.md
```

---

## ⚙️ Kurulum

### 1. Ön Gereksinimler

- Python 3.12+
- PostgreSQL 14+
- Finnhub API anahtarı → https://finnhub.io
- Telegram Bot Token → @BotFather
- Telegram Chat ID → @userinfobot

### 2. Ortam Kurulumu

```bash
git clone <repo_url>
cd nasdaq_bot

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt

# spaCy İngilizce modeli (zorunlu)
python -m spacy download en_core_web_sm
```

### 3. Ortam Değişkenleri

```bash
cp .env .env
# .env dosyasını düzenle:
```

```env
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
```

### 4. PostgreSQL Veritabanı

```sql
CREATE DATABASE nasdaq_news;
```

### 5. DB Şemasını Kur

```bash
python main.py --init-db
```

---

## 🚀 Çalıştırma

| Komut | Açıklama |
|-------|----------|
| `python main.py` | Sürekli döngü (varsayılan, `FETCH_INTERVAL_SECONDS` aralığında) |
| `python main.py --once` | Tek sefer çalıştır (cron uyumlu) |
| `python main.py --init-db` | Sadece DB şemasını kur |
| `python main.py --stats` | DB istatistiklerini ekrana yaz |
| `python main.py --summary` | Telegram'a günlük özet gönder |
| `python main.py --test-telegram` | Telegram bağlantısını test et |

### Cron Örneği (her 5 dakika)

```cron
*/5 * * * * /path/to/.venv/bin/python /path/to/nasdaq_bot/main.py --once >> /var/log/nasdaq_bot.log 2>&1
```

### Systemd Servisi (sürekli döngü)

```ini
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
```

---

## 📊 Veritabanı Şeması

### `news_articles`

| Kolon | Tür | Açıklama |
|-------|-----|----------|
| `article_id` | TEXT UNIQUE | Finnhub benzersiz ID |
| `headline` | TEXT | Haber başlığı |
| `summary` | TEXT | Özet |
| `source` | TEXT | Haber kaynağı |
| `url` | TEXT | Bağlantı |
| `published_at` | TIMESTAMPTZ | Yayın tarihi |
| `sentiment` | TEXT | positive/negative/neutral |
| `confidence` | NUMERIC | 0.0–1.0 |
| `impact_level` | TEXT | LOW/MEDIUM/HIGH |
| `affected_tickers` | TEXT[] | NVDA, AMD… |
| `raw_json` | JSONB | Orijinal API cevabı |

### `ticker_mentions`

| Kolon | Tür | Açıklama |
|-------|-----|----------|
| `article_id` | TEXT | FK → news_articles |
| `ticker` | TEXT | NVDA |
| `company` | TEXT | Nvidia |

---

## 📱 Telegram Çıktı Örneği

```
🚨 NASDAQ HABER
━━━━━━━━━━━━━━━━━━━━━━━━━
📰 Başlık: Nvidia Posts Record Q2 Revenue, Beats Estimates
🏢 Şirketler: NVDA, AMD, INTC
🟢 Duygu: Pozitif
🎯 Güven Puanı: %91
📈 Etki Seviyesi: HIGH
📡 Kaynak: Reuters
🕐 Tarih: 2024-08-28 18:34 UTC

━━━━━━━━━━━━━━━━━━━━━━━━━
📊 TRADE SİNYALİ
⚡ İşlem: 🟢 AL
🎯 Semboller: NVDA, AMD, INTC
🎲 Güven: %91
🔵 Risk: LOW
💡 Gerekçe: NVDA, AMD, INTC için pozitif haber akışı tespit edildi
   (%91 güven). Etki seviyesi yüksek — kısa/orta vadeli AL fırsatı
   değerlendirilebilir.
```

---

## 🧪 Test

```bash
# Telegram bağlantısı
python main.py --test-telegram

# Tek döngü (DB + Telegram)
python main.py --once

# İstatistikler
python main.py --stats
```

---

## 🔧 Geliştirme

### Trade Sinyal Mantığını Özelleştirme

`trade_signal.py` içindeki `_DECISION_MATRIX` değiştirilerek
strateji kolayca uyarlanabilir:

```python
_DECISION_MATRIX = {
    ("positive", "HIGH"):   ("BUY",   "LOW"),
    ("negative", "HIGH"):   ("SELL",  "HIGH"),
    ...
}
```

### Yeni Ticker Ekleme

`entity_extractor.py` → `TICKER_MAP` sözlüğüne ekle:

```python
"your company": "TICK",
```

---

## ⚠️ Sorumluluk Reddi

Bu bot **yalnızca araştırma ve bilgilendirme** amaçlıdır.
Üretilen trade sinyalleri **yatırım tavsiyesi değildir**.
Gerçek para ile kullanmadan önce bir finansal danışmana başvurunuz.
