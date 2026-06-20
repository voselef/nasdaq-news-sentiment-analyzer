"""
gemini_client.py - Gemini destekli haber yorumu ve ticker etki analizi.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

import requests

import config
import database

logger = logging.getLogger("nasdaq_bot.gemini")

_STATE_LAST_CALL_KEY = "gemini_last_call_at"
_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,7}$")


class GeminiClient:
    """Gemini REST API ile haberden etkilenen hisseleri ve kisa AI notunu alir."""

    def __init__(self) -> None:
        self._api_key = config.GEMINI_API_KEY
        self._model = config.GEMINI_MODEL
        self._min_interval = config.GEMINI_MIN_INTERVAL_SECONDS
        self._timeout = config.GEMINI_TIMEOUT_SECONDS
        self._session = requests.Session()

    def analyze_article(
        self,
        article: dict,
        candidate_tickers: Optional[list[str]] = None,
        force: bool = False,
    ) -> Optional[dict]:
        if not self._api_key:
            logger.debug("GEMINI_API_KEY tanimli degil; Gemini analizi atlandi.")
            return None

        if not force and not self._can_call_now():
            logger.info("Gemini dakika limiti nedeniyle AI yorumu atlandi.")
            return None

        if not force:
            self._mark_call_now()

        prompt = _build_prompt(article, candidate_tickers or [])
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 512,
                "responseMimeType": "application/json",
            },
        }
        url = _API_URL.format(model=self._model)

        try:
            response = self._session.post(
                url,
                headers={
                    "x-goog-api-key": self._api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self._timeout,
            )
            if response.status_code in {429, 500, 502, 503, 504}:
                logger.warning(
                    "Gemini gecici/kota hatasi (%s); AI'siz rapor kullanilacak.",
                    response.status_code,
                )
                return None
            response.raise_for_status()
            return _parse_response(response.json(), self._model)
        except requests.exceptions.RequestException as exc:
            logger.warning("Gemini istegi basarisiz; AI'siz rapor kullanilacak: %s", exc)
            return None
        except Exception as exc:
            logger.warning("Gemini cevabi islenemedi; AI'siz rapor kullanilacak: %s", exc)
            return None

    def _can_call_now(self) -> bool:
        try:
            last_call = database.get_state(_STATE_LAST_CALL_KEY)
            if not last_call:
                return True
            return time.time() - float(last_call) >= self._min_interval
        except Exception as exc:
            logger.warning("Gemini limit durumu okunamadi; AI yorumu atlandi: %s", exc)
            return False

    def _mark_call_now(self) -> None:
        try:
            database.set_state(_STATE_LAST_CALL_KEY, str(time.time()))
        except Exception as exc:
            logger.warning("Gemini limit durumu yazilamadi: %s", exc)


def _build_prompt(article: dict, candidate_tickers: list[str]) -> str:
    headline = article.get("headline") or ""
    summary = article.get("summary") or ""
    source = article.get("source") or ""
    published_at = article.get("published_at") or ""
    candidates = ", ".join(candidate_tickers) if candidate_tickers else "Yok"

    return f"""
You are a cautious US stock market news analyst.
Analyze the news and identify which publicly traded stock tickers are likely affected.
Use only tickers that are directly and reasonably connected to the news.
You may use the candidate tickers, but you must correct them if the text implies different stocks.

Return only valid JSON with this shape:
{{
  "affected_tickers": ["NVDA", "AMD"],
  "ai_note": "Turkish, max 450 chars. Brief actionable-style note without claiming certainty.",
  "confidence": 0.0
}}

Candidate tickers from local rules: {candidates}
Source: {source}
Published at: {published_at}
Headline: {headline}
Summary: {summary}
""".strip()


def _parse_response(data: dict, model: str) -> Optional[dict]:
    text = (
        data.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [{}])[0]
        .get("text", "")
    )
    if not text:
        return None

    parsed = json.loads(text)
    tickers = _clean_tickers(parsed.get("affected_tickers", []))
    note = str(parsed.get("ai_note", "")).strip()
    confidence = _safe_float(parsed.get("confidence", 0.0))

    if not tickers and not note:
        return None

    return {
        "provider": "gemini",
        "model": model,
        "affected_tickers": tickers,
        "ai_note": note[:700],
        "confidence": confidence,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
    }


def _clean_tickers(raw_tickers) -> list[str]:
    if not isinstance(raw_tickers, list):
        return []

    tickers: list[str] = []
    for raw in raw_tickers:
        ticker = str(raw).strip().upper()
        if _TICKER_RE.match(ticker) and ticker not in tickers:
            tickers.append(ticker)
    return tickers[:10]


def _safe_float(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
