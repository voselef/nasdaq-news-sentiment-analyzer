"""
finnhub_client.py – Finnhub REST API istemcisi.
NASDAQ genel haberlerini ve şirket haberlerini çeker.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import requests

import config

logger = logging.getLogger("nasdaq_bot.finnhub")

# Finnhub ücretsiz plan: 60 istek/dakika
_RATE_LIMIT_SLEEP: float = 1.1  # istek arası minimum bekleme (saniye)


class FinnhubClient:
    """Finnhub API ile iletişim kuran istemci sınıfı."""

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "X-Finnhub-Token": config.FINNHUB_API_KEY,
                "Accept": "application/json",
                "User-Agent": "nasdaq-bot/1.0",
            }
        )
        self._last_request_time: float = 0.0

    # ─── Dahili Yardımcılar ──────────────────────────────────────────────────────

    def _throttle(self) -> None:
        """Rate limit ihlalini önlemek için gerekirse bekler."""
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < _RATE_LIMIT_SLEEP:
            time.sleep(_RATE_LIMIT_SLEEP - elapsed)

    def _get(self, endpoint: str, params: Optional[dict] = None) -> dict | list:
        """GET isteği gönderir; HTTP ve ağ hatalarını yakalar."""
        self._throttle()
        url = f"{config.FINNHUB_BASE_URL}{endpoint}"
        try:
            resp = self._session.get(url, params=params or {}, timeout=15)
            resp.raise_for_status()
            self._last_request_time = time.monotonic()
            return resp.json()
        except requests.exceptions.Timeout:
            logger.error("Finnhub isteği zaman aşımına uğradı: %s", endpoint)
            raise
        except requests.exceptions.HTTPError as exc:
            logger.error(
                "Finnhub HTTP hatası %s – %s: %s",
                exc.response.status_code,
                endpoint,
                exc.response.text[:200],
            )
            raise
        except requests.exceptions.RequestException as exc:
            logger.error("Finnhub ağ hatası: %s", exc)
            raise

    # ─── Haber Çekme ────────────────────────────────────────────────────────────

    def fetch_market_news(self, category: str = "general") -> list[dict]:
        """
        Piyasa haberlerini çeker.
        category: 'general' | 'forex' | 'crypto' | 'merger'
        """
        logger.debug("Piyasa haberleri çekiliyor (kategori=%s)…", category)
        try:
            raw: list = self._get("/news", {"category": category})  # type: ignore[assignment]
            articles = self._normalize(raw)
            logger.info(
                "%d adet piyasa haberi alındı (kategori=%s).", len(articles), category
            )
            return articles
        except Exception as exc:
            logger.error("Piyasa haberleri alınamadı: %s", exc)
            return []

    def fetch_company_news(
        self,
        symbol: str,
        from_date: str,
        to_date: str,
    ) -> list[dict]:
        """
        Belirli bir şirketin haberlerini çeker.
        from_date / to_date: 'YYYY-MM-DD' formatında.
        """
        logger.debug("Şirket haberleri çekiliyor: %s", symbol)
        try:
            raw: list = self._get(  # type: ignore[assignment]
                "/company-news",
                {"symbol": symbol, "from": from_date, "to": to_date},
            )
            articles = self._normalize(raw)
            logger.info("%d adet haber alındı (%s).", len(articles), symbol)
            return articles
        except Exception as exc:
            logger.error("Şirket haberleri alınamadı (%s): %s", symbol, exc)
            return []

    # ─── Normalleştirme ─────────────────────────────────────────────────────────

    @staticmethod
    def _normalize(raw_list: list) -> list[dict]:
        """
        Finnhub'ın ham cevabını uygulama içi standart formata dönüştürür.
        Eksik / geçersiz kayıtları filtreler.
        """
        normalized: list[dict] = []
        for item in raw_list:
            try:
                article_id = str(item.get("id", ""))
                headline = (item.get("headline") or "").strip()
                if not article_id or not headline:
                    continue  # minimum gereklilik

                ts = item.get("datetime", 0)
                published_at = datetime.fromtimestamp(ts, tz=timezone.utc)

                normalized.append(
                    {
                        "article_id": article_id,
                        "headline": headline,
                        "summary": (item.get("summary") or "").strip() or None,
                        "source": (item.get("source") or "").strip() or None,
                        "url": (item.get("url") or "").strip() or None,
                        "published_at": published_at,
                        "raw": item,  # orijinal veri saklanır
                    }
                )
            except Exception as exc:
                logger.warning("Haber normalleştirme hatası: %s | kayıt: %s", exc, item)

        # Yeniden eskiye sırala
        normalized.sort(key=lambda x: x["published_at"], reverse=True)
        return normalized[: config.MAX_NEWS_PER_FETCH]
