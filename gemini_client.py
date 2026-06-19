"""
gemini_client.py - Gemini ile haberin etkiledigi hisseleri ve AI notunu uretir.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import requests

import config

logger = logging.getLogger("nasdaq_bot.gemini")

_GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


class GeminiClient:
    """Google Gemini REST API icin hafif istemci."""

    def __init__(self) -> None:
        self._api_key = config.GEMINI_API_KEY
        self._model = config.GEMINI_MODEL
        self._session = requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    def analyze_news(self, article: dict, fallback_tickers: list[str]) -> dict | None:
        """Haber metnini Gemini'ye yollar; basarisiz olursa None doner."""
        if not self.enabled:
            logger.info("Gemini API anahtari yok; AI yorumlama atlandi.")
            return None

        prompt = _build_prompt(article, fallback_tickers)
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 512,
                "responseMimeType": "application/json",
            },
        }
        url = _GEMINI_ENDPOINT.format(model=self._model)
        headers = {
            "x-goog-api-key": self._api_key,
            "Content-Type": "application/json",
        }

        try:
            response = self._session.post(
                url,
                headers=headers,
                json=payload,
                timeout=config.GEMINI_TIMEOUT_SECONDS,
            )
            if response.status_code in (429, 403):
                logger.warning(
                    "Gemini limit/yetki hatasi (%s); AI'siz rapora geciliyor.",
                    response.status_code,
                )
                return None
            response.raise_for_status()
            text = _extract_response_text(response.json())
            parsed = _parse_json_object(text)
            if parsed is None:
                logger.warning("Gemini cevabi JSON olarak okunamadi: %s", text[:200])
                return None

            tickers = _clean_tickers(parsed.get("affected_tickers"))
            note = str(parsed.get("ai_note") or "").strip()
            if not note:
                note = "Gemini haberi inceledi ancak ek yorum uretmedi."

            return {
                "affected_tickers": tickers,
                "ai_note": note[:900],
                "ai_provider": "gemini",
                "ai_model": self._model,
            }
        except requests.exceptions.RequestException as exc:
            logger.warning("Gemini API hatasi; AI'siz rapora geciliyor: %s", exc)
            return None
        except Exception as exc:
            logger.warning("Gemini yorumlama hatasi; AI'siz rapora geciliyor: %s", exc)
            return None


def _build_prompt(article: dict, fallback_tickers: list[str]) -> str:
    headline = article.get("headline") or ""
    summary = article.get("summary") or ""
    source = article.get("source") or ""
    published_at = article.get("published_at") or ""
    fallback = ", ".join(fallback_tickers) if fallback_tickers else "Yok"

    return f"""
Sen ABD/NASDAQ haberlerini yorumlayan dikkatli bir finans haber analistisin.
Bu haber hangi halka acik sirket hisselerini etkileyebilir, bunu belirle.

Kurallar:
- Sadece haber metninden makul sekilde etkilenebilecek borsa sembollerini yaz.
- Emin degilsen bos liste dondur.
- Sembol formatini BUY/SELL degil, sadece ticker olarak ver: NVDA, AAPL gibi.
- ai_note kisa Turkce olsun: neden bu hisseler etkilenebilir ve izleme onerisi.
- Yatirim tavsiyesi verme; "izlenebilir", "risk takip edilmeli" gibi not yaz.
- Cevabi sadece JSON olarak dondur.

JSON semasi:
{{
  "affected_tickers": ["TICKER1", "TICKER2"],
  "ai_note": "Kisa Turkce yorum"
}}

Haber:
Baslik: {headline}
Ozet/Icerik: {summary}
Kaynak: {source}
Tarih: {published_at}
Kelime tabanli mevcut tespit: {fallback}
""".strip()


def _extract_response_text(data: dict[str, Any]) -> str:
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    parts = candidates[0].get("content", {}).get("parts", [])
    return "".join(str(part.get("text", "")) for part in parts)


def _parse_json_object(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None


def _clean_tickers(value) -> list[str]:
    if not isinstance(value, list):
        return []

    tickers: list[str] = []
    for item in value:
        ticker = str(item or "").strip().upper()
        if ticker and ticker.replace(".", "").replace("-", "").isalnum():
            tickers.append(ticker[:12])
    return list(dict.fromkeys(tickers))
