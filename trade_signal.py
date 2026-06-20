"""
trade_signal.py – Trade Sinyal Üretim Motoru.

Duygu analizi + etki seviyesi + ticker bağlamını birleştirerek
BUY / SELL / HOLD / WATCH sinyalleri üretir.
Her sinyal risk skoru, gerekçe ve önerilen eylem içerir.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("nasdaq_bot.trade_signal")

from config import CONFIDENCE_LEVEL

# ─── Sinyal Sabitleri ───────────────────────────────────────────────────────────

class Action:
    BUY   = "BUY"
    SELL  = "SELL"
    HOLD  = "HOLD"
    WATCH = "WATCH"   # İzle, henüz işlem yapma


class RiskLevel:
    LOW    = "LOW"
    MEDIUM = "MEDIUM"
    HIGH   = "HIGH"


# ─── Sinyal Dataclass ───────────────────────────────────────────────────────────

@dataclass
class TradeSignal:
    """Bir haber makalesinden üretilen trade sinyali."""
    action: str                          # BUY | SELL | HOLD | WATCH
    risk_level: str                      # LOW | MEDIUM | HIGH
    confidence: float                    # 0.0 – 1.0
    tickers: list[str]                   # Etkilenen semboller
    rationale: str                       # İnsan okunabilir gerekçe
    sentiment: str                       # Kaynak duygu
    impact_level: str                    # LOW | MEDIUM | HIGH
    generated_at: str = field(
        default_factory=lambda: datetime.now(tz=timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)

    def telegram_summary(self) -> str:
        """Telegram mesajına eklenecek kısa sinyal özeti."""
        action_emoji = {
            "BUY":   "🟢 AL",
            "SELL":  "🔴 SAT",
            "HOLD":  "🟡 BEKLE",
            "WATCH": "👁 İZLE",
        }.get(self.action, self.action)

        risk_emoji = {
            "LOW":    "🔵",
            "MEDIUM": "🟠",
            "HIGH":   "🔴",
        }.get(self.risk_level, "⚪")

        tickers_str = ", ".join(self.tickers) if self.tickers else "—"
        conf_pct = f"%{int(self.confidence * 100)}"

        return (
            "\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>TRADE SİNYALİ</b>\n"
            f"⚡ <b>İşlem:</b> {action_emoji}\n"
            f"🎯 <b>Semboller:</b> <code>{tickers_str}</code>\n"
            f"🎲 <b>Güven:</b> {conf_pct}\n"
            f"{risk_emoji} <b>Risk:</b> {self.risk_level}\n"
            f"💡 <b>Gerekçe:</b> <i>{self.rationale}</i>"
        )


# ─── Kural Tablosu ──────────────────────────────────────────────────────────────
#
# Karar matrisi:  (sentiment, impact_level) → (action, risk_level)
#
_DECISION_MATRIX: dict[tuple[str, str], tuple[str, str]] = {
    # Pozitif haberler
    ("positive", "HIGH"):   (Action.BUY,   RiskLevel.LOW),
    ("positive", "MEDIUM"): (Action.BUY,   RiskLevel.MEDIUM),
    ("positive", "LOW"):    (Action.WATCH,  RiskLevel.LOW),
    # Negatif haberler
    ("negative", "HIGH"):   (Action.SELL,  RiskLevel.HIGH),
    ("negative", "MEDIUM"): (Action.SELL,  RiskLevel.MEDIUM),
    ("negative", "LOW"):    (Action.WATCH,  RiskLevel.LOW),
    # Nötr haberler
    ("neutral",  "HIGH"):   (Action.WATCH, RiskLevel.MEDIUM),
    ("neutral",  "MEDIUM"): (Action.HOLD,  RiskLevel.LOW),
    ("neutral",  "LOW"):    (Action.HOLD,  RiskLevel.LOW),
}

# Minimum güven eşiği: altındaysa sinyal üretilmez (HOLD)
_MIN_CONFIDENCE_FOR_SIGNAL = CONFIDENCE_LEVEL

# Ticker yoksa işlem önerilmez
_NO_TICKER_ACTION = Action.WATCH


# ─── Gerekçe Şablonları ─────────────────────────────────────────────────────────

def _build_rationale(
    action: str,
    sentiment: str,
    impact: str,
    confidence: float,
    tickers: list[str],
    headline: str,
) -> str:
    """İnsan okunabilir Türkçe gerekçe metni oluşturur."""
    ticker_str = ", ".join(tickers) if tickers else "tespit edilemeyen semboller"
    conf_pct = int(confidence * 100)
    sentiment_tr = {"positive": "pozitif", "negative": "negatif", "neutral": "nötr"}.get(
        sentiment, sentiment
    )
    impact_tr = {"HIGH": "yüksek", "MEDIUM": "orta", "LOW": "düşük"}.get(impact, impact)

    templates = {
        Action.BUY: (
            f"{ticker_str} için {sentiment_tr} haber akışı tespit edildi "
            f"(%{conf_pct} güven). Etki seviyesi {impact_tr} — "
            f"kısa/orta vadeli AL fırsatı değerlendirilebilir. "
            f'Haber: "{headline[:60]}…"'
        ),
        Action.SELL: (
            f"{ticker_str} için {impact_tr} etkili {sentiment_tr} haber. "
            f"FinBERT güveni %{conf_pct}. Risk yönetimi açısından "
            f"pozisyon azaltma veya SAT sinyali güçlü. "
            f'Tetikleyen: "{headline[:60]}…"'
        ),
        Action.WATCH: (
            f"{ticker_str} için {sentiment_tr} sinyal var ancak "
            f"güven (%{conf_pct}) veya etki ({impact_tr}) henüz işlem için yetersiz. "
            f"İzleme listesine ekle, teyit gelince harekete geç."
        ),
        Action.HOLD: (
            f"Nötr veya düşük etkili haber akışı ({impact_tr} etki, %{conf_pct} güven). "
            f"Mevcut {ticker_str} pozisyonlarında değişiklik önerilmez."
        ),
    }
    return templates.get(action, "Yeterli veri yok.")


# ─── Risk Çarpanları ────────────────────────────────────────────────────────────

def _apply_risk_modifiers(
    base_risk: str,
    confidence: float,
    tickers: list[str],
) -> str:
    """Düşük güven veya çok fazla ticker risk seviyesini artırabilir."""
    if confidence < 0.60:
        # Güven düşükse risk bir basamak artır
        bump = {RiskLevel.LOW: RiskLevel.MEDIUM, RiskLevel.MEDIUM: RiskLevel.HIGH}
        return bump.get(base_risk, base_risk)
    if len(tickers) > 5:
        # Çok fazla farklı ticker → sektörel haber, risk artar
        bump = {RiskLevel.LOW: RiskLevel.MEDIUM, RiskLevel.MEDIUM: RiskLevel.HIGH}
        return bump.get(base_risk, base_risk)
    return base_risk


# ─── Ana Motor ──────────────────────────────────────────────────────────────────

class TradeSignalEngine:
    """
    Haber analizinden trade sinyali üreten kural + skor tabanlı motor.

    İlerisi için: bu sınıf ML tabanlı bir model ile değiştirilebilir.
    """

    def generate(self, article: dict, analysis: dict) -> Optional[TradeSignal]:
        """
        Makale + duygu analizi alır, TradeSignal döndürür.
        İşlem yapılamayacak durumlarda None döner.
        """
        sentiment    = analysis.get("sentiment", "neutral")
        confidence   = float(analysis.get("confidence_score", 0.0))
        impact       = analysis.get("impact_level", "LOW")
        tickers      = analysis.get("affected_tickers", [])
        headline     = article.get("headline", "")

        # ── Güven filtresi ──────────────────────────────────────────────────────
        if confidence < _MIN_CONFIDENCE_FOR_SIGNAL:
            logger.debug(
                "Güven çok düşük (%.2f) – sinyal üretilmiyor: %s",
                confidence, article.get("article_id"),
            )
            return TradeSignal(
                action=Action.HOLD,
                risk_level=RiskLevel.LOW,
                confidence=confidence,
                tickers=tickers,
                rationale=f"Güven skoru çok düşük (%{int(confidence*100)}). İşlem önerilmez.",
                sentiment=sentiment,
                impact_level=impact,
            )

        # ── Ticker filtresi ─────────────────────────────────────────────────────
        if not tickers:
            return TradeSignal(
                action=_NO_TICKER_ACTION,
                risk_level=RiskLevel.MEDIUM,
                confidence=confidence,
                tickers=[],
                rationale="Etkilenen şirket/sembol tespit edilemedi. İzleme modunda kal.",
                sentiment=sentiment,
                impact_level=impact,
            )

        # ── Karar matrisi ───────────────────────────────────────────────────────
        action, base_risk = _DECISION_MATRIX.get(
            (sentiment, impact),
            (Action.WATCH, RiskLevel.MEDIUM),   # fallback
        )

        # Risk çarpanı uygula
        final_risk = _apply_risk_modifiers(base_risk, confidence, tickers)

        # Gerekçe oluştur
        rationale = _build_rationale(
            action, sentiment, impact, confidence, tickers, headline
        )

        signal = TradeSignal(
            action=action,
            risk_level=final_risk,
            confidence=confidence,
            tickers=tickers,
            rationale=rationale,
            sentiment=sentiment,
            impact_level=impact,
        )

        logger.info(
            "Sinyal üretildi → %s | %s | risk=%s | conf=%.2f | tickers=%s",
            signal.action,
            signal.impact_level,
            signal.risk_level,
            signal.confidence,
            signal.tickers,
        )
        return signal
