# config.py
"""
Конфигурация генератора синтетических данных FinGuard.

Содержит:
- VOLUMES — целевые объёмы сущностей;
- справочники (страны, MCC, риски, типы карт и т.д.);
- согласованные словари (MCC_BY_CATEGORY, RISK_BY_CATEGORY, RISK_WEIGHTS_BY_COUNTRY);
- GenConfig — dataclass с параметрами запуска.
"""
from dataclasses import dataclass


VOLUMES = {
    "client": 10_000,
    "account": 12_000,
    "card": 15_000,
    "device": 20_000,
    "merchant": 2_000,
    "transaction": 500_000,     # нижняя граница: rapid_transactions добавляет сверху
    "authorization": 300_000,   # используется как цель в transactions.py
}

ANOMALY_RATE = 0.04          # 4% аномалий (в диапазоне 3–5%)
SEED = 42

COUNTRIES_CITIES = {
    "RU": ["Москва", "Санкт-Петербург", "Казань", "Новосибирск", "Екатеринбург"],
    "KZ": ["Алматы", "Астана"],
    "BY": ["Минск"],
    "TR": ["Istanbul", "Antalya"],
    "AE": ["Dubai"],
    "GE": ["Tbilisi"],
    "US": ["New York", "San Francisco"],
}

# «Иностранные» для RU-клиента направления. Для клиентов из других стран
# список пересчитывается динамически (см. _pick_foreign_country в anomalies.py).
FOREIGN_COUNTRIES = ["TR", "AE", "GE", "US", "KZ", "BY"]

RISK_LEVELS = ["low", "standard", "standard", "standard", "high"]
KYC_STATUSES = ["verified", "verified", "verified", "pending", "rejected"]
CARD_TYPES = ["virtual", "physical"]
PAYMENT_SYSTEMS = ["MIR", "VISA", "MasterCard"]
CARD_STATUSES = ["active"] * 9 + ["blocked"]
DEVICE_TYPES = ["mobile", "desktop", "tablet"]
OS_BY_TYPE = {
    "mobile": ["iOS", "Android"],
    "desktop": ["Windows", "macOS", "Linux"],
    "tablet": ["iOS", "Android"],
}

TX_TYPES = ["purchase", "transfer", "topup", "withdrawal", "refund", "international"]
CURRENCIES = ["RUB"] * 8 + ["USD", "EUR", "TRY"]
TX_STATUSES = ["approved"] * 8 + ["declined"] * 2

# MCC-коды, сгруппированные по категории мерчанта.
# Гарантирует согласованность merchant.category ↔ merchant.mcc.
MCC_CODES = ["5411", "5812", "5912", "7995", "4111", "6011", "4814", "5732"]

MCC_BY_CATEGORY = {
    "retail":    ["5411", "5732"],          # супермаркет, электроника
    "food":      ["5812"],                  # рестораны
    "transport": ["4111", "4814"],          # транспорт, телеком
    "gambling":  ["7995"],                  # азартные игры
    "crypto":    ["6011"],                  # крипто-обменники
    "utilities": ["5912"],                  # аптеки/ЖКХ
}

# Риск мерчанта зависит от категории: gambling/crypto — чаще high.
RISK_BY_CATEGORY = {
    "retail":    ["low", "standard", "standard", "standard", "high"],
    "food":      ["low", "low", "standard", "standard", "high"],
    "transport": ["low", "standard", "standard", "standard", "high"],
    "gambling":  ["standard", "high", "high", "high", "high"],
    "crypto":    ["standard", "high", "high", "high", "high"],
    "utilities": ["low", "low", "standard", "standard", "high"],
}

# Веса стран для клиентов: RU доминирует, остальные — реже.
COUNTRY_WEIGHTS_CLIENT = [70, 8, 6, 6, 4, 3, 3]

# Риск клиента зависит от страны: высокорисковые юрисдикции → чаще high.
RISK_WEIGHTS_BY_COUNTRY = {
    "RU": [10, 55, 35],   # low, standard, high
    "KZ": [10, 55, 35],
    "BY": [10, 55, 35],
    "TR": [5, 35, 60],
    "AE": [5, 35, 60],
    "GE": [5, 35, 60],
    "US": [5, 35, 60],
}
RISK_ORDER = ["low", "standard", "high"]

# KYC зависит от risk_level: low → verified, high → чаще pending/rejected.
KYC_WEIGHTS_BY_RISK = {
    "low":      [90, 8, 2],
    "standard": [80, 15, 5],
    "high":     [50, 30, 20],
}
KYC_ORDER = ["verified", "pending", "rejected"]

# Валюта операции: локальные — RUB, международные — валюта страны мерчанта.
CURRENCY_BY_COUNTRY = {
    "RU": "RUB", "KZ": "KZT", "BY": "BYN",
    "TR": "TRY", "AE": "AED", "GE": "GEL", "US": "USD",
}

ANOMALY_SCENARIOS = [
    "rapid_transactions",
    "unusual_country",
    "new_device",
    "large_amount",
    "night_activity",
    "multiple_declines",
    "cashout_pattern",
    "account_takeover_pattern",
]


@dataclass
class GenConfig:
    """Параметры запуска генератора. seed используется в main.py для RNG."""
    seed: int = SEED
    anomaly_rate: float = ANOMALY_RATE
    start_date: str = "2025-01-01"
    end_date: str = "2026-12-31"