"""
main.py – NASDAQ Haber Analiz & Trade Sinyal Botu
Ana orkestrasyon motoru: tüm modülleri birleştirir ve döngüyü yönetir.

Akış:
  Finnhub → Yeni Haber? → EntityExtractor → SentimentAnalyzer
  → TradeSignalEngine → DB Kayıt → Telegram Bildirimi → Tekrar
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import schedule

import config
import database
from finnhub_client import FinnhubClient
from entity_extractor import EntityExtractor
from gemini_client import GeminiClient
from sentiment import SentimentAnalyzer
from telegram_bot import TelegramBot
from trade_signal import TradeSignalEngine

logger = logging.getLogger("nasdaq_bot.main")

# ─── Graceful Shutdown ──────────────────────────────────────────────────────────
_SHUTDOWN = False
_NEWS_TRACKING_BOOTSTRAPPED_KEY = "news_tracking_bootstrapped"


def _handle_signal(signum, frame):  # noqa: ANN001
    global _SHUTDOWN
    logger.info("Kapatma sinyali alındı (%s). Bot durduruluyor…", signum)
    _SHUTDOWN = True


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ─── Pipeline ───────────────────────────────────────────────────────────────────

class NasdaqBot:
    """
    Tüm alt modülleri bir araya getiren ana bot sınıfı.

    Sorumluluklar:
    - Finnhub'dan haber çekme
    - NER ile şirket/ticker tespiti
    - FinBERT ile duygu analizi
    - Trade sinyali üretimi
    - PostgreSQL'e kayıt (duplicate engelleme)
    - Telegram bildirimi
    - Günlük özet
    """

    def __init__(self) -> None:
        logger.info("━━━ NASDAQ BOT BAŞLATILIYOR ━━━")
        logger.info("Modüller yükleniyor…")

        self.finnhub = FinnhubClient()
        self.extractor = EntityExtractor()
        self.analyzer = SentimentAnalyzer()
        self.gemini = GeminiClient()
        self.signal_engine = TradeSignalEngine()
        self.telegram = TelegramBot()

        self._processed_count = 0
        self._error_count = 0
        self._session_start = datetime.now(tz=timezone.utc)

        logger.info("Tüm modüller hazır. Bot çalışıyor.")

    # ─── Tek Makale İşleme ──────────────────────────────────────────────────────

    def _process_article(self, article: dict) -> Optional[dict]:
        """
        Bir makaleyi uçtan uca işler:
        Haber → NER → Duygu → Sinyal → DB → Telegram

        Başarıda zenginleştirilmiş article dict'ini döndürür, hata/skip'te None.
        """
        article_id = article.get("article_id", "unknown")

        # 1) Duplicate kontrolü (hızlı DB sorgusu)
        try:
            if database.article_exists(article_id):
                logger.debug("Zaten işlendi, atlanıyor: %s", article_id)
                return None
        except Exception as exc:
            logger.error("DB kontrol hatası (%s): %s", article_id, exc)
            return None

        headline = article.get("headline", "")
        summary = article.get("summary", "") or ""
        full_text = f"{headline}. {summary}".strip()

        logger.info("İşleniyor → %s", headline[:80])

        # 2) NER: şirket & ticker tespiti
        try:
            ticker_mentions = self.extractor.extract(full_text)
            article["ticker_mentions"] = ticker_mentions
        except Exception as exc:
            logger.warning("NER hatası (%s): %s", article_id, exc)
            article["ticker_mentions"] = []

        # 3) FinBERT duygu analizi
        try:
            analysis = self.analyzer.analyze_article(article)
        except Exception as exc:
            logger.warning("Duygu analizi hatası (%s): %s", article_id, exc)
            analysis = {
                "sentiment": "neutral",
                "confidence_score": 0.0,
                "impact_level": "LOW",
                "affected_tickers": [],
            }

        # 4) Gemini ile ticker listesi ve AI notu zenginlestirme
        try:
            gemini_analysis = self.gemini.analyze_article(
                article,
                analysis.get("affected_tickers", []),
            )
            if gemini_analysis:
                gemini_tickers = gemini_analysis.get("affected_tickers", [])
                if gemini_tickers:
                    analysis["affected_tickers"] = gemini_tickers
                analysis["ai_note"] = gemini_analysis.get("ai_note")
                analysis["ai_provider"] = gemini_analysis.get("provider")
                analysis["ai_model"] = gemini_analysis.get("model")
                analysis["ai_tickers"] = gemini_tickers
                analysis["ai_confidence"] = gemini_analysis.get("confidence")
        except Exception as exc:
            logger.warning("Gemini analizi atlandi (%s): %s", article_id, exc)

        # 5) Trade sinyali üretimi
        try:
            trade_signal = self.signal_engine.generate(article, analysis)
            analysis["trade_signal"] = trade_signal
        except Exception as exc:
            logger.warning("Trade sinyal hatası (%s): %s", article_id, exc)
            analysis["trade_signal"] = None

        # 6) DB kaydı için veri hazırlama
        affected_tickers = analysis.get("affected_tickers", [])
        db_record = {
            "article_id": article_id,
            "headline": headline,
            "summary": summary or None,
            "source": article.get("source"),
            "url": article.get("url"),
            "published_at": article.get("published_at"),
            "sentiment": analysis.get("sentiment"),
            "confidence": analysis.get("confidence_score"),
            "impact_level": analysis.get("impact_level"),
            "affected_tickers": affected_tickers if affected_tickers else None,
            "ai_note": analysis.get("ai_note"),
            "ai_provider": analysis.get("ai_provider"),
            "ai_model": analysis.get("ai_model"),
            "ai_tickers": analysis.get("ai_tickers"),
            "raw_json": json.dumps(article.get("raw", {}), default=str),
            "ticker_mentions": article.get("ticker_mentions", []),
        }

        # 7) DB kayıt
        try:
            saved = database.save_article(db_record)
            if not saved:
                logger.debug("DB çakışması – atlanıyor: %s", article_id)
                return None
        except Exception as exc:
            logger.error("DB kayıt hatası (%s): %s", article_id, exc)
            self._error_count += 1
            return None

        # 8) Telegram bildirimi
        try:
            self.telegram.send_news_alert(article, analysis)
        except Exception as exc:
            logger.error("Telegram gönderim hatası (%s): %s", article_id, exc)
            # Telegram hatası işlemeyi durdurmaz

        self._processed_count += 1
        logger.info(
            "✓ Kaydedildi | %s | %s (%.0f%%) | impact=%s | sinyal=%s",
            article_id,
            analysis.get("sentiment", "?").upper(),
            (analysis.get("confidence_score", 0)) * 100,
            analysis.get("impact_level", "?"),
            analysis["trade_signal"].action if analysis.get("trade_signal") else "—"
        )

        article["analysis"] = analysis
        return article

    # ─── Fetch Döngüsü ──────────────────────────────────────────────────────────

    def fetch_and_process(self) -> int:
        """
        Haberleri çeker ve pipeline'dan geçirir.
        İşlenen yeni haber sayısını döndürür.
        """
        logger.info("─── Yeni haber çekme başlıyor ───")
        new_count = 0

        try:
            articles = self.finnhub.fetch_market_news(category="general")
        except Exception as exc:
            logger.error("Haber çekme başarısız: %s", exc)
            try:
                self.telegram.send_error_alert(f"Haber çekme hatası: {exc}")
            except Exception:
                pass
            return 0

        logger.info("%d adet haber alındı, pipeline başlıyor…", len(articles))

        if not articles:
            logger.info("Islenecek haber yok.")
            return 0

        try:
            bootstrapped = (
                database.get_state(_NEWS_TRACKING_BOOTSTRAPPED_KEY) == "1"
            )
        except Exception as exc:
            logger.error("Haber takip durumu okunamadi: %s", exc)
            return 0

        if not bootstrapped:
            article_ids = [
                str(article.get("article_id"))
                for article in articles
                if article.get("article_id")
            ]
            seeded_count = database.mark_articles_seen(article_ids)
            database.set_state(_NEWS_TRACKING_BOOTSTRAPPED_KEY, "1")
            logger.info(
                "Ilk takip baslangici: %d mevcut haber goruldu sayildi; "
                "Telegram bildirimi gonderilmedi.",
                seeded_count,
            )
            return 0

        new_articles = []
        for article in articles:
            article_id = str(article.get("article_id", ""))
            if not article_id:
                continue

            try:
                seen = database.article_seen(article_id)
                processed = database.article_exists(article_id)
                if seen or processed:
                    if processed and not seen:
                        database.mark_article_seen(article_id)
                    continue
            except Exception as exc:
                logger.error("Haber tekrar kontrolu hatasi (%s): %s", article_id, exc)
                continue

            new_articles.append(article)

        logger.info("%d yeni haber bulundu, pipeline basliyor.", len(new_articles))

        for article in reversed(new_articles):
            if _SHUTDOWN:
                break
            article_id = str(article.get("article_id", ""))
            result = self._process_article(article)
            if result is not None:
                new_count += 1
            if article_id:
                try:
                    database.mark_article_seen(article_id)
                except Exception as exc:
                    logger.error("Haber goruldu kaydi hatasi (%s): %s", article_id, exc)
            time.sleep(0.1)  # DB baskısını azalt

        logger.info(
            "─── Döngü tamamlandı | %d yeni / %d toplam alındı ───",
            new_count,
            len(articles),
        )
        return new_count

    # ─── Günlük Özet ────────────────────────────────────────────────────────────

    def send_daily_summary(self) -> None:
        """PostgreSQL'den istatistik çekip Telegram'a özet gönderir."""
        try:
            stats = database.get_stats()
            uptime = datetime.now(tz=timezone.utc) - self._session_start
            hours, rem = divmod(int(uptime.total_seconds()), 3600)
            minutes = rem // 60
            stats["uptime"] = f"{hours}s {minutes}d"
            stats["session_processed"] = self._processed_count
            stats["session_errors"] = self._error_count
            self.telegram.send_daily_summary(stats)
            logger.info("Günlük özet Telegram'a gönderildi.")
        except Exception as exc:
            logger.error("Günlük özet hatası: %s", exc)

    # ─── Ana Döngü ──────────────────────────────────────────────────────────────

    def run(self) -> None:
        """
        Scheduler ile periyodik haber çekme döngüsünü başlatır.
        SIGINT / SIGTERM ile graceful shutdown.
        """
        interval = config.FETCH_INTERVAL_SECONDS
        logger.info(
            "Bot aktif | haber çekme aralığı: %d saniye (~%.1f dk)",
            interval,
            interval / 60,
        )

        # İlk çalışmayı hemen yap
        self.fetch_and_process()

        # Periyodik görevler
        schedule.every(interval).seconds.do(self.fetch_and_process)
        schedule.every().day.at("08:00").do(self.send_daily_summary)

        logger.info("Scheduler çalışıyor. Çıkmak için Ctrl+C.")
        while not _SHUTDOWN:
            schedule.run_pending()
            time.sleep(1)

        logger.info("Bot durduruldu. Toplam işlenen: %d | Hata: %d",
                    self._processed_count, self._error_count)

    # ─── Tek Sefer Çalıştırma ───────────────────────────────────────────────────

    def run_once(self) -> int:
        """Tek bir fetch+process döngüsü çalıştırır (test/cron modu)."""
        return self.fetch_and_process()

    def resend_last_news_report(self) -> bool:
        """Veritabanindaki en son haber raporunu Telegram'a tekrar gonderir."""
        latest = database.get_latest_article()
        if latest is None:
            logger.warning("Tekrar gonderilecek kayitli haber bulunamadi.")
            return False

        affected_tickers = _json_list(latest.get("affected_tickers"))
        if not affected_tickers:
            affected_tickers = [
                mention["ticker"]
                for mention in latest.get("ticker_mentions", [])
                if mention.get("ticker")
            ]

        article = {
            "article_id": latest.get("article_id"),
            "headline": latest.get("headline"),
            "summary": latest.get("summary"),
            "source": latest.get("source"),
            "url": latest.get("url"),
            "published_at": latest.get("published_at"),
            "ticker_mentions": latest.get("ticker_mentions", []),
            "raw": _json_value(latest.get("raw_json"), {}),
        }
        analysis = {
            "sentiment": latest.get("sentiment") or "neutral",
            "confidence_score": float(latest.get("confidence") or 0.0),
            "impact_level": latest.get("impact_level") or "LOW",
            "affected_tickers": affected_tickers,
            "ai_note": latest.get("ai_note"),
            "ai_provider": latest.get("ai_provider"),
            "ai_model": latest.get("ai_model"),
            "ai_tickers": _json_list(latest.get("ai_tickers")),
        }

        try:
            analysis["trade_signal"] = self.signal_engine.generate(article, analysis)
        except Exception as exc:
            logger.warning("Son haber icin trade sinyali uretilemedi: %s", exc)
            analysis["trade_signal"] = None

        logger.info("Son haber raporu tekrar gonderiliyor: %s", article["article_id"])
        return self.telegram.send_news_alert(article, analysis)

    def test_last_news_ai(self, force: bool = False) -> bool:
        """Veritabanindaki son haberi Gemini ile test eder ve sonucu yazdirir."""
        latest = database.get_latest_article()
        if latest is None:
            print("Kayitli haber bulunamadi.")
            return False

        affected_tickers = _json_list(latest.get("affected_tickers"))
        if not affected_tickers:
            affected_tickers = [
                mention["ticker"]
                for mention in latest.get("ticker_mentions", [])
                if mention.get("ticker")
            ]

        article = {
            "article_id": latest.get("article_id"),
            "headline": latest.get("headline"),
            "summary": latest.get("summary"),
            "source": latest.get("source"),
            "url": latest.get("url"),
            "published_at": latest.get("published_at"),
            "ticker_mentions": latest.get("ticker_mentions", []),
            "raw": _json_value(latest.get("raw_json"), {}),
        }

        result = self.gemini.analyze_article(article, affected_tickers, force=force)
        output = {
            "article_id": article.get("article_id"),
            "headline": article.get("headline"),
            "local_candidate_tickers": affected_tickers,
            "gemini_result": result,
        }
        print(json.dumps(output, indent=2, ensure_ascii=False, default=str))
        return result is not None


# ─── CLI ────────────────────────────────────────────────────────────────────────

def _json_value(value, default):
    if value in (None, ""):
        return default

    parsed = value
    for _ in range(2):
        if not isinstance(parsed, str):
            return parsed
        try:
            parsed = json.loads(parsed)
        except (TypeError, json.JSONDecodeError):
            return default
    return parsed


def _json_list(value) -> list:
    parsed = _json_value(value, [])
    return parsed if isinstance(parsed, list) else []


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="NASDAQ Haber Analiz & Trade Sinyal Botu",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Örnekler:
  python main.py                  # Sürekli döngü modu
  python main.py --once           # Tek sefer çalıştır
  python main.py --init-db        # Sadece DB şemasını kur
  python main.py --stats          # DB istatistiklerini göster
  python main.py --summary        # Telegram'a özet gönder
        """,
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Tek bir fetch döngüsü çalıştırıp çıkar (cron uyumlu)",
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Veritabanı tablolarını oluştur/güncelle ve çık",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Veritabanı istatistiklerini ekrana yazdır",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Telegram'a günlük özet gönder",
    )
    parser.add_argument(
        "--lastnew",
        action="store_true",
        help="Kayitli son haber raporunu Telegram'a tekrar gonder",
    )
    parser.add_argument(
        "--test-ai",
        action="store_true",
        help="Kayitli son haberi Gemini ile test et ve sonucu ekrana yazdir",
    )
    parser.add_argument(
        "--force-ai",
        action="store_true",
        help="--test-ai icin Gemini dakika limitini bypass et",
    )
    parser.add_argument(
        "--test-telegram",
        action="store_true",
        help="Telegram bağlantısını test et",
    )
    return parser.parse_args()


def _print_banner() -> None:
    banner = r"""
╔══════════════════════════════════════════════════════════╗
║          NASDAQ HABER ANALİZ & TRADE SİNYAL BOTU        ║
║          FinBERT  │  spaCy  │  Finnhub  │  Telegram      ║
╚══════════════════════════════════════════════════════════╝
    """
    print(banner)


# ─── Entry Point ────────────────────────────────────────────────────────────────

def main() -> None:
    _print_banner()
    args = _parse_args()

    # ── Sadece DB init ──────────────────────────────────────────────────────────
    if args.init_db:
        logger.info("Veritabanı şeması kuruluyor…")
        database.initialize_db()
        logger.info("Tamamlandı.")
        sys.exit(0)

    # ── DB istatistikleri ───────────────────────────────────────────────────────
    if args.stats:
        database.initialize_db()
        stats = database.get_stats()
        print(json.dumps(stats, indent=2, default=str))
        recent = database.get_recent_articles(10)
        print("\n── Son 10 Haber ──")
        for a in recent:
            print(
                f"  [{a['sentiment']:<8}] [{a['impact_level']:<6}] "
                f"{str(a['published_at'])[:16]}  {a['headline'][:70]}"
            )
        sys.exit(0)

    # ── DB başlatma (tüm diğer modlar için) ─────────────────────────────────────
    try:
        database.initialize_db()
    except Exception as exc:
        logger.critical("Veritabanına bağlanılamadı: %s", exc)
        sys.exit(1)

    bot = NasdaqBot()

    # ── Telegram test ───────────────────────────────────────────────────────────
    if args.test_telegram:
        logger.info("Telegram bağlantısı test ediliyor…")
        ok = bot.telegram.send_message(
            "✅ <b>NASDAQ BOT</b> – Telegram bağlantısı başarılı!\n"
            f"🕐 {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )
        print("Telegram testi:", "BAŞARILI ✓" if ok else "BAŞARISIZ ✗")
        sys.exit(0 if ok else 1)

    # ── Günlük özet ─────────────────────────────────────────────────────────────
    if args.summary:
        bot.send_daily_summary()
        sys.exit(0)

    # ── Tek sefer ───────────────────────────────────────────────────────────────
    if args.lastnew:
        ok = bot.resend_last_news_report()
        sys.exit(0 if ok else 1)

    if args.test_ai:
        ok = bot.test_last_news_ai(force=args.force_ai)
        sys.exit(0 if ok else 1)

    if args.once:
        count = bot.run_once()
        logger.info("Tek sefer çalışma tamamlandı. Yeni haber: %d", count)
        sys.exit(0)

    # ── Sürekli döngü (varsayılan) ───────────────────────────────────────────────
    bot.run()


if __name__ == "__main__":
    main()
