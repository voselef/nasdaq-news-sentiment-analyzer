"""
telegram_bot.py – Telegram Bot API ile bildirim gönderimi.
Analiz edilmiş haberleri biçimlendirilmiş mesaj olarak iletir.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import requests

import config

logger = logging.getLogger("nasdaq_bot.telegram")

_TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
_MAX_MESSAGE_LENGTH = 4096
_RETRY_ATTEMPTS = 3
_RETRY_SLEEP = 2.0  # saniye


# ─── Emoji Sabitleri ─────────────────────────────────────────────────────────────
_SENTIMENT_EMOJI = {
    "positive": "🟢",
    "negative": "🔴",
    "neutral": "🟡",
}
_IMPACT_EMOJI = {
    "HIGH": "🚨",
    "MEDIUM": "⚠️",
    "LOW": "ℹ️",
}


class TelegramBot:
    """Telegram Bot API istemcisi."""

    def __init__(self) -> None:
        self._token = config.TELEGRAM_BOT_TOKEN
        self._chat_id = config.TELEGRAM_CHAT_ID
        self._session = requests.Session()

    # ─── Düşük Seviye Gönderim ──────────────────────────────────────────────────

    def _send_request(self, method: str, payload: dict) -> bool:
        """Telegram API'ye istek gönderir; başarıda True döner."""
        url = _TELEGRAM_API.format(token=self._token, method=method)
        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            try:
                resp = self._session.post(url, json=payload, timeout=15)
                data = resp.json()
                if data.get("ok"):
                    return True
                error = data.get("description", "Bilinmeyen hata")
                logger.warning("Telegram API hatası (deneme %d): %s", attempt, error)
                # 429 Too Many Requests
                if resp.status_code == 429:
                    retry_after = data.get("parameters", {}).get("retry_after", 5)
                    logger.info("Rate limit – %s saniye bekleniyor…", retry_after)
                    time.sleep(retry_after)
            except requests.exceptions.RequestException as exc:
                logger.warning("Telegram ağ hatası (deneme %d): %s", attempt, exc)
            if attempt < _RETRY_ATTEMPTS:
                time.sleep(_RETRY_SLEEP)
        logger.error("Telegram mesajı gönderilemedi: %d deneme başarısız.", _RETRY_ATTEMPTS)
        return False

    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Ham metin mesajı gönderir."""
        # Çok uzun mesajları böl
        chunks = _split_message(text, _MAX_MESSAGE_LENGTH)
        success = True
        for chunk in chunks:
            payload = {
                "chat_id": self._chat_id,
                "text": chunk,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }
            if not self._send_request("sendMessage", payload):
                success = False
        return success

    # ─── Haber Mesajı Oluşturucu ────────────────────────────────────────────────

    def send_news_alert(self, article: dict, analysis: dict) -> bool:
        """
        Zenginleştirilmiş haber makalesini Telegram bildirimine dönüştürür.
        Trade sinyali varsa mesaja eklenir.

        article anahtarları: headline, summary, source, url, published_at
        analysis anahtarları: sentiment, confidence_score, impact_level,
                               affected_tickers, trade_signal (opsiyonel)
        """
        message = _format_news_message(article, analysis)

        # Trade sinyali varsa mesaja ekle
        trade_signal = analysis.get("trade_signal")
        if trade_signal is not None:
            try:
                message += trade_signal.telegram_summary()
            except Exception:
                pass  # Sinyal formatı hata verirse haberi yine de gönder

        success = self.send_message(message)
        if success:
            logger.info(
                "Telegram bildirimi gönderildi: %s [%s]",
                article.get("article_id", "?"),
                analysis.get("sentiment", "?"),
            )
        return success

    def send_daily_summary(self, stats: dict) -> bool:
        """Günlük özet mesajı gönderir."""
        now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        by_s = stats.get("by_sentiment", {})
        text = (
            "📊 <b>NASDAQ BOT – GÜNLÜK ÖZET</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 <i>{now}</i>\n\n"
            f"📰 Toplam Haber: <b>{stats.get('total_articles', 0)}</b>\n"
            f"🟢 Pozitif: <b>{by_s.get('positive', 0)}</b>\n"
            f"🔴 Negatif: <b>{by_s.get('negative', 0)}</b>\n"
            f"🟡 Nötr: <b>{by_s.get('neutral', 0)}</b>\n"
            f"\n⏱ Çalışma Süresi: <b>{stats.get('uptime', '—')}</b>\n"
            f"✅ Bu Oturumda İşlenen: <b>{stats.get('session_processed', 0)}</b>\n"
            f"⚠️ Hata Sayısı: <b>{stats.get('session_errors', 0)}</b>"
        )
        return self.send_message(text)

    def send_error_alert(self, error_msg: str) -> bool:
        """Kritik sistem hatalarını bildirir."""
        text = (
            "🔧 <b>NASDAQ BOT – SİSTEM HATASI</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<code>{_escape_html(str(error_msg)[:500])}</code>"
        )
        return self.send_message(text)


# ─── Mesaj Biçimlendirici ────────────────────────────────────────────────────────

def _format_news_message(article: dict, analysis: dict) -> str:
    """Standart haber bildirimi metnini oluşturur."""
    sentiment = analysis.get("sentiment", "neutral")
    confidence = analysis.get("confidence_score", 0.0)
    impact = analysis.get("impact_level", "LOW")
    tickers = analysis.get("affected_tickers", [])

    s_emoji = _SENTIMENT_EMOJI.get(sentiment, "🟡")
    i_emoji = _IMPACT_EMOJI.get(impact, "ℹ️")
    sentiment_tr = {"positive": "Pozitif", "negative": "Negatif", "neutral": "Nötr"}.get(
        sentiment, "Nötr"
    )

    headline = _escape_html(article.get("headline", "—"))
    source = _escape_html(article.get("source", "—"))
    url = article.get("url", "")
    published_at = article.get("published_at")

    if isinstance(published_at, datetime):
        date_str = published_at.strftime("%Y-%m-%d %H:%M UTC")
    else:
        date_str = str(published_at or "—")

    tickers_str = ", ".join(tickers) if tickers else "Tespit edilemedi"
    confidence_pct = f"%{int(confidence * 100)}"

    headline_display = (
        f'<a href="{url}">{headline}</a>' if url else f"<b>{headline}</b>"
    )

    lines = [
        f"{i_emoji} <b>NASDAQ HABER</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📰 <b>Başlık:</b> {headline_display}",
        "",
        f"🏢 <b>Şirketler:</b> <code>{tickers_str}</code>",
        f"{s_emoji} <b>Duygu:</b> {sentiment_tr}",
        f"🎯 <b>Güven Puanı:</b> {confidence_pct}",
        f"📈 <b>Etki Seviyesi:</b> {impact}",
        "",
        f"📡 <b>Kaynak:</b> {source}",
        f"🕐 <b>Tarih:</b> <i>{date_str}</i>",
    ]
    return "\n".join(lines)


def _escape_html(text: str) -> str:
    """HTML özel karakterlerini kaçırır."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _split_message(text: str, max_len: int) -> list[str]:
    """Telegram mesaj limitini aşan metni böler."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:max_len])
        text = text[max_len:]
    return chunks
