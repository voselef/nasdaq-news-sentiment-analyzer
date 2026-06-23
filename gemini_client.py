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

logger = logging.getLogger("nasdaq_bot.ai")

_STATE_LAST_CALL_KEY = "ai_last_call_at"
_API_URL = "https://openrouter.ai/api/v1/chat/completions"

_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,7}$")


class AIClient:
    def __init__(self) -> None:
        self._api_key = config.OPENROUTER_API_KEY
        self._model = config.OPENROUTER_MODEL
        self._min_interval = config.AI_MIN_INTERVAL_SECONDS
        self._timeout = config.AI_TIMEOUT_SECONDS
        self._session = requests.Session()

    def analyze_article(
        self,
        article: dict,
        candidate_tickers: Optional[list[str]] = None,
        force: bool = False,
    ) -> Optional[dict]:

        if not self._api_key:
            logger.warning("API yokluğu nedeniyle atlandı")
            return None

        if not force and not self._can_call_now():
            logger.warning("AI rate limit nedeniyle atlandı")
            return None

        if not force:
            self._mark_call_now()

        prompt = _build_prompt(article, candidate_tickers or [])

        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a cautious US stock market news analyst. "
                        "Return ONLY valid JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }

        try:
            response = self._session.post(
                _API_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self._timeout,
            )

            if response.status_code in {429, 500, 502, 503, 504}:
                return None

            response.raise_for_status()

            return _parse_response(
                response.json(),
                self._model,
            )

        except requests.exceptions.RequestException as exc:
            logger.warning("OpenRouter request failed: %s", exc)
            return None

    def _can_call_now(self) -> bool:
        try:
            last_call = database.get_state(_STATE_LAST_CALL_KEY)

            if not last_call:
                return True

            return time.time() - float(last_call) >= self._min_interval

        except Exception:
            return False

    def _mark_call_now(self) -> None:
        try:
            database.set_state(
                _STATE_LAST_CALL_KEY,
                str(time.time()),
            )
        except Exception:
            pass


def _build_prompt(article: dict, candidate_tickers: list[str]) -> str:
    headline = article.get("headline") or ""
    summary = article.get("summary") or ""
    source = article.get("source") or ""
    published_at = article.get("published_at") or ""

    candidates = (
        ", ".join(candidate_tickers)
        if candidate_tickers
        else "Yok"
    )

    return f"""
Analyze the news article.

Return JSON:

{{
  "affected_tickers": ["NVDA","AMD"],
  "ai_note": "Turkish explanation",
  "confidence": 0.0
}}

Candidate tickers: {candidates}

Source: {source}
Published at: {published_at}

Headline:
{headline}

Summary:
{summary}
"""


def _parse_response(data: dict, model: str) -> Optional[dict]:

    try:
        text = data["choices"][0]["message"]["content"]

        parsed = json.loads(text)

        tickers = _clean_tickers(
            parsed.get("affected_tickers", [])
        )

        note = str(
            parsed.get("ai_note", "")
        ).strip()

        confidence = _safe_float(
            parsed.get("confidence", 0.0)
        )

        return {
            "provider": "openrouter",
            "model": model,
            "affected_tickers": tickers,
            "ai_note": note[:700],
            "confidence": confidence,
            "generated_at": datetime.now(
                tz=timezone.utc
            ).isoformat(),
        }

    except Exception:
        return None


def _clean_tickers(raw_tickers) -> list[str]:

    if not isinstance(raw_tickers, list):
        return []

    result = []

    for raw in raw_tickers:
        ticker = str(raw).strip().upper()

        if _TICKER_RE.match(ticker):
            if ticker not in result:
                result.append(ticker)

    return result[:10]


def _safe_float(value) -> float:

    try:
        return max(
            0.0,
            min(1.0, float(value)),
        )
    except Exception:
        return 0.0