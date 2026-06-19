"""
sentiment.py – FinBERT tabanlı finansal duygu analizi.
Model: ProsusAI/finbert (Hugging Face)
Çıktı: sentiment, confidence_score, impact_level
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Optional

import config

logger = logging.getLogger("nasdaq_bot.sentiment")

# ─── Model Yükleyici ────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _load_pipeline():
    """FinBERT pipeline'ı yalnızca bir kez yükler (lazy loading)."""
    try:
        from transformers import pipeline
        logger.info("FinBERT modeli yükleniyor: %s …", config.FINBERT_MODEL)
        pipe = pipeline(
            "text-classification",
            model=config.FINBERT_MODEL,
            top_k=None,           # tüm sınıf skorları
            truncation=True,
            max_length=512,
        )
        logger.info("FinBERT modeli hazır.")
        return pipe
    except Exception as exc:
        logger.error("FinBERT yüklenemedi: %s", exc)
        return None


# ─── Yardımcı Fonksiyonlar ──────────────────────────────────────────────────────

def _determine_impact(confidence: float) -> str:
    if confidence >= config.IMPACT_HIGH_THRESHOLD:
        return "HIGH"
    if confidence >= config.IMPACT_MEDIUM_THRESHOLD:
        return "MEDIUM"
    return "LOW"


def _parse_finbert_output(raw_output: list[dict]) -> dict:
    """
    FinBERT çıktısını ayrıştırır.
    raw_output örneği: [{"label": "positive", "score": 0.87}, ...]
    """
    scores: dict[str, float] = {item["label"].lower(): item["score"] for item in raw_output}

    # En yüksek skoru bul
    best_label = max(scores, key=lambda k: scores[k])
    best_score = scores[best_label]

    # Normalize et: positive/negative/neutral
    label_map = {
        "positive": "positive",
        "negative": "negative",
        "neutral": "neutral",
    }
    sentiment = label_map.get(best_label, "neutral")

    return {
        "sentiment": sentiment,
        "confidence_score": round(best_score, 4),
        "all_scores": {k: round(v, 4) for k, v in scores.items()},
        "impact_level": _determine_impact(best_score),
    }


# ─── Fallback Analiz ────────────────────────────────────────────────────────────

_POSITIVE_WORDS = {
    "surge", "jump", "soar", "gain", "rise", "beat", "record", "growth",
    "profit", "revenue", "bullish", "upgrade", "buy", "strong", "outperform",
    "rally", "upside", "opportunity", "positive", "exceed", "approve", "launch",
    "partnership", "acquisition", "dividend", "buyback",
}

_NEGATIVE_WORDS = {
    "drop", "fall", "plunge", "loss", "decline", "miss", "cut", "layoff",
    "downgrade", "sell", "weak", "underperform", "bearish", "risk", "fear",
    "crash", "recession", "debt", "lawsuit", "investigation", "fraud",
    "recall", "delay", "cancel", "concern", "warning", "downside",
}


def _fallback_sentiment(text: str) -> dict:
    """FinBERT yoksa basit kelime sayımına dayalı analiz."""
    words = set(text.lower().split())
    pos_count = len(words & _POSITIVE_WORDS)
    neg_count = len(words & _NEGATIVE_WORDS)

    if pos_count > neg_count:
        sentiment, confidence = "positive", min(0.5 + pos_count * 0.05, 0.75)
    elif neg_count > pos_count:
        sentiment, confidence = "negative", min(0.5 + neg_count * 0.05, 0.75)
    else:
        sentiment, confidence = "neutral", 0.50

    return {
        "sentiment": sentiment,
        "confidence_score": round(confidence, 4),
        "all_scores": {sentiment: confidence},
        "impact_level": _determine_impact(confidence),
    }


# ─── Ana Sınıf ──────────────────────────────────────────────────────────────────

class SentimentAnalyzer:
    """
    FinBERT ile finansal duygu analizi.
    Model yüklenemezse kelime bazlı fallback'e geçer.
    """

    def __init__(self) -> None:
        self._pipeline = _load_pipeline()
        if self._pipeline is None:
            logger.warning("FinBERT kullanılamıyor → kelime tabanlı fallback aktif.")

    def analyze(
        self,
        text: str,
        tickers: Optional[list[str]] = None,
    ) -> dict:
        """
        Verilen metni analiz eder.

        Döner:
        {
            "sentiment":       "positive" | "negative" | "neutral",
            "confidence_score": 0.0 – 1.0,
            "impact_level":    "LOW" | "MEDIUM" | "HIGH",
            "affected_tickers": [...],
            "all_scores":      {"positive": ..., "negative": ..., "neutral": ...},
        }
        """
        if not text or not text.strip():
            return self._empty_result(tickers)

        # Başlık + özet birleştir (max 512 token uyumlu)
        input_text = text[:1024]

        try:
            if self._pipeline:
                raw = self._pipeline(input_text)
                # pipeline(..., top_k=None) liste içinde liste döner
                if isinstance(raw, list) and isinstance(raw[0], list):
                    raw = raw[0]
                result = _parse_finbert_output(raw)
            else:
                result = _fallback_sentiment(input_text)
        except Exception as exc:
            logger.warning("Duygu analizi hatası: %s → fallback", exc)
            result = _fallback_sentiment(input_text)

        result["affected_tickers"] = tickers or []

        logger.debug(
            "Analiz sonucu – duygu: %s (%.2f) | impact: %s | tickerlar: %s",
            result["sentiment"],
            result["confidence_score"],
            result["impact_level"],
            result["affected_tickers"],
        )
        return result

    def analyze_article(self, article: dict) -> dict:
        """
        Finnhub makale dict'ini alır; başlık + özeti birleştirip analiz eder.
        Dönen değer orijinal makale dict'ine eklenerek zenginleştirilir.
        """
        headline = article.get("headline", "")
        summary = article.get("summary", "") or ""
        combined_text = f"{headline}. {summary}".strip()
        tickers = [m["ticker"] for m in article.get("ticker_mentions", [])]
        return self.analyze(combined_text, tickers)

    @staticmethod
    def _empty_result(tickers: Optional[list[str]]) -> dict:
        return {
            "sentiment": "neutral",
            "confidence_score": 0.0,
            "impact_level": "LOW",
            "affected_tickers": tickers or [],
            "all_scores": {},
        }

    @staticmethod
    def to_json(result: dict) -> str:
        """Analiz sonucunu JSON string olarak döndürür."""
        return json.dumps(result, ensure_ascii=False, indent=2)
