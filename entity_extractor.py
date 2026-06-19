"""
entity_extractor.py – spaCy ile varlık tanıma ve ticker eşleştirme.
ORG etiketli varlıkları tespit edip NASDAQ sembollerine eşler.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Optional

logger = logging.getLogger("nasdaq_bot.entity_extractor")

# ─── NASDAQ Ticker Sözlüğü ──────────────────────────────────────────────────────
# Şirket adı (küçük harf, temizlenmiş) → ticker sembolü
TICKER_MAP: dict[str, str] = {
    # Mega Cap Tech
    "apple": "AAPL",
    "apple inc": "AAPL",
    "microsoft": "MSFT",
    "microsoft corporation": "MSFT",
    "alphabet": "GOOGL",
    "google": "GOOGL",
    "amazon": "AMZN",
    "amazon.com": "AMZN",
    "meta": "META",
    "meta platforms": "META",
    "facebook": "META",
    "nvidia": "NVDA",
    "nvidia corporation": "NVDA",
    "tesla": "TSLA",
    "broadcom": "AVGO",
    # Semiconductors
    "intel": "INTC",
    "intel corporation": "INTC",
    "amd": "AMD",
    "advanced micro devices": "AMD",
    "qualcomm": "QCOM",
    "micron": "MU",
    "micron technology": "MU",
    "texas instruments": "TXN",
    "applied materials": "AMAT",
    "lam research": "LRCX",
    "kla": "KLAC",
    "marvell": "MRVL",
    "marvell technology": "MRVL",
    "monolithic power systems": "MPWR",
    "on semiconductor": "ON",
    "skyworks": "SWKS",
    "qorvo": "QRVO",
    "wolfspeed": "WOLF",
    "arm holdings": "ARM",
    "arm": "ARM",
    # Cloud & SaaS
    "salesforce": "CRM",
    "servicenow": "NOW",
    "workday": "WDAY",
    "adobe": "ADBE",
    "oracle": "ORCL",
    "sap": "SAP",
    "snowflake": "SNOW",
    "palantir": "PLTR",
    "datadog": "DDOG",
    "crowdstrike": "CRWD",
    "palo alto networks": "PANW",
    "fortinet": "FTNT",
    "zscaler": "ZS",
    "okta": "OKTA",
    "veeva systems": "VEEV",
    "hubspot": "HUBS",
    "zendesk": "ZEN",
    "twilio": "TWLO",
    "mongodb": "MDB",
    "elastic": "ESTC",
    "confluent": "CFLT",
    "hashicorp": "HCP",
    "gitlab": "GTLB",
    "github": "MSFT",   # Microsoft bünyesinde
    # E-Commerce & Consumer Tech
    "netflix": "NFLX",
    "spotify": "SPOT",
    "ebay": "EBAY",
    "etsy": "ETSY",
    "wayfair": "W",
    "shopify": "SHOP",
    "mercadolibre": "MELI",
    "pinduoduo": "PDD",
    "jd.com": "JD",
    "baidu": "BIDU",
    # Fintech & Payments
    "paypal": "PYPL",
    "paypal holdings": "PYPL",
    "square": "SQ",
    "block": "SQ",
    "coinbase": "COIN",
    "robinhood": "HOOD",
    "affirm": "AFRM",
    "upstart": "UPST",
    "sofi": "SOFI",
    "sofi technologies": "SOFI",
    "bill.com": "BILL",
    "adyen": "ADYEY",
    # Biotech & Healthcare
    "moderna": "MRNA",
    "biogen": "BIIB",
    "regeneron": "REGN",
    "vertex pharmaceuticals": "VRTX",
    "illumina": "ILMN",
    "align technology": "ALGN",
    "intuitive surgical": "ISRG",
    "idexx laboratories": "IDXX",
    "dexcom": "DXCM",
    "agilent": "A",
    # Communications
    "comcast": "CMCSA",
    "t-mobile": "TMUS",
    "charter communications": "CHTR",
    "lumen technologies": "LUMN",
    # Electric Vehicles & Clean Energy
    "lucid": "LCID",
    "lucid group": "LCID",
    "rivian": "RIVN",
    "nio": "NIO",
    "li auto": "LI",
    "xpeng": "XPEV",
    "plug power": "PLUG",
    "enphase": "ENPH",
    "enphase energy": "ENPH",
    "solaredge": "SEDG",
    "first solar": "FSLR",
    # Travel & Hospitality
    "booking holdings": "BKNG",
    "booking.com": "BKNG",
    "airbnb": "ABNB",
    "expedia": "EXPE",
    "tripadvisor": "TRIP",
    "lyft": "LYFT",
    "uber": "UBER",
    "doordash": "DASH",
    # Video Games
    "activision": "ATVI",
    "ea": "EA",
    "electronic arts": "EA",
    "take-two": "TTWO",
    "take two": "TTWO",
    "roblox": "RBLX",
    "unity": "U",
    # Other Notable
    "zoom": "ZM",
    "zoom video": "ZM",
    "docusign": "DOCU",
    "peloton": "PTON",
    "chewy": "CHWY",
    "lululemon": "LULU",
    "monster beverage": "MNST",
    "starbucks": "SBUX",
    "pepsico": "PEP",
    "costco": "COST",
    "dollar tree": "DLTR",
    "dollar general": "DG",
    "ross stores": "ROST",
    "tractor supply": "TSCO",
    "cintas": "CTAS",
    "fastenal": "FAST",
    "graco": "GGG",
    "verisk": "VRSK",
    "iqvia": "IQV",
    "gartner": "IT",
    "fiserv": "FI",
    "automatic data processing": "ADP",
    "adp": "ADP",
    "paychex": "PAYX",
    "intuit": "INTU",
    "cadence design": "CDNS",
    "synopsys": "SNPS",
    "ansys": "ANSS",
    "autodesk": "ADSK",
    "ptc": "PTC",
    "trimble": "TRMB",
    "costar": "CSGP",
    "zillow": "Z",
    "redfin": "RDFN",
}

# Ek keyword → ticker (gazete haberlerinde sıkça geçen kısaltmalar)
_KEYWORD_MAP: dict[str, str] = {
    "nvda": "NVDA",
    "msft": "MSFT",
    "aapl": "AAPL",
    "amzn": "AMZN",
    "googl": "GOOGL",
    "goog": "GOOGL",
    "tsla": "TSLA",
    "meta": "META",
    "nflx": "NFLX",
    "avgo": "AVGO",
}


# ─── spaCy Yükleyici ────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _load_nlp():
    """spaCy modelini yalnızca bir kez yükler."""
    try:
        import spacy
        nlp = spacy.load("en_core_web_sm")
        logger.info("spaCy modeli yüklendi: en_core_web_sm")
        return nlp
    except OSError:
        logger.warning(
            "spaCy modeli bulunamadı. "
            "Lütfen: python -m spacy download en_core_web_sm"
        )
        return None


# ─── Yardımcı Fonksiyonlar ──────────────────────────────────────────────────────

def _clean(text: str) -> str:
    """Eşleştirme için metni normalleştirir."""
    text = text.lower().strip()
    # Noktalama işaretlerini kaldır (kısa çizgi hariç)
    text = re.sub(r"[^\w\s\-\.]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _match_ticker(entity_text: str) -> Optional[str]:
    """Bir varlık metninden ticker sembolü bulmaya çalışır."""
    cleaned = _clean(entity_text)

    # Doğrudan eşleşme
    if cleaned in TICKER_MAP:
        return TICKER_MAP[cleaned]

    # Keyword tablosu (büyük harf kısaltmalar)
    upper = entity_text.upper().strip()
    if upper in _KEYWORD_MAP:
        return _KEYWORD_MAP[upper]

    # Kısmi eşleşme: sözlükteki anahtar temiz metni içeriyor mu?
    for key, ticker in TICKER_MAP.items():
        if key in cleaned or cleaned in key:
            return ticker

    return None


def _extract_inline_tickers(text: str) -> list[str]:
    """
    Metin içinde TICKER formatında ($NVDA veya parantez içi (NVDA)) geçen
    sembolleri regex ile bulur.
    """
    # $TICKER veya (TICKER) formatları
    pattern = r"\$([A-Z]{2,5})\b|\(([A-Z]{2,5})\)"
    found: list[str] = []
    for match in re.finditer(pattern, text):
        ticker = match.group(1) or match.group(2)
        found.append(ticker)
    return list(set(found))


# ─── Ana Arayüz ─────────────────────────────────────────────────────────────────

class EntityExtractor:
    """spaCy ile NER; şirket adlarını ticker sembollerine eşler."""

    def __init__(self) -> None:
        self._nlp = _load_nlp()

    def extract(self, text: str) -> list[dict]:
        """
        Metin içindeki şirket/kuruluş varlıklarını tespit eder.
        Döner: [{"ticker": "NVDA", "company": "Nvidia"}, ...]
        """
        results: dict[str, dict] = {}  # ticker → kayıt (dedup)

        # 1) Inline ticker tespiti ($NVDA, (NVDA) formatları)
        for ticker in _extract_inline_tickers(text):
            if ticker not in results:
                results[ticker] = {"ticker": ticker, "company": None}

        # 2) spaCy NER (kullanılabiliyorsa)
        if self._nlp and text:
            try:
                doc = self._nlp(text[:5000])  # model sınırı
                for ent in doc.ents:
                    if ent.label_ in ("ORG", "PRODUCT"):
                        ticker = _match_ticker(ent.text)
                        if ticker and ticker not in results:
                            results[ticker] = {
                                "ticker": ticker,
                                "company": ent.text,
                            }
            except Exception as exc:
                logger.warning("NER hatası: %s", exc)

        # 3) Keyword taraması (backup)
        lower_text = text.lower()
        for name, ticker in TICKER_MAP.items():
            if ticker not in results and name in lower_text:
                results[ticker] = {"ticker": ticker, "company": name.title()}

        mentions = list(results.values())
        logger.debug(
            "Bulunan tickerlar: %s",
            [m["ticker"] for m in mentions] or "—",
        )
        return mentions

    def get_tickers(self, text: str) -> list[str]:
        """Sadece ticker listesini döndürür."""
        return [m["ticker"] for m in self.extract(text)]
