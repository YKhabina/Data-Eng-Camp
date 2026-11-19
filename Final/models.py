# models.py
"""
Dataclass-схемы сущностей.

Правила:
- Строковые поля (amount, balance) — осознанно, чтобы RAW-слой сохранял формат.
- Служебные поля с префиксом '_' НЕ уходят в Kafka (см. to_kafka).
"""
from dataclasses import dataclass, field, asdict
from typing import List, Optional


@dataclass
class Client:
    client_id: str
    created_at: str
    country: str
    city: str
    risk_level: str
    kyc_status: str


@dataclass
class Account:
    account_id: str
    client_id: str
    account_type: str          # wallet / savings
    currency: str
    balance: str               # String, как amount
    opened_at: str
    status: str


@dataclass
class Card:
    card_id: str
    client_id: str
    card_type: str
    payment_system: str
    status: str
    issued_at: str
    expires_at: str


@dataclass
class Device:
    device_id: str
    client_id: str
    device_type: str
    os: str
    trusted: bool
    first_seen_at: str


@dataclass
class Merchant:
    merchant_id: str
    name: str
    category: str
    mcc: str
    country: str
    city: str
    risk_level: str


@dataclass
class Transaction:
    transaction_id: str
    client_id: str
    card_id: str
    merchant_id: str
    amount: str                # String — RAW сохраняет формат
    currency: str
    transaction_type: str
    country: str               # страна совершения операции (может ≠ merchant.country
                               # у аномалий unusual_country/account_takeover — см. anomalies.py)
    city: str
    device_id: str
    event_datetime: str
    status: str

    # Служебные поля, НЕ попадают в Kafka.
    # _anomaly — метка сценария для отчёта/валидации.
    # _behavior_day — дата для будущих агрегатов (не используется сейчас).
    _anomaly: Optional[str] = field(default=None, repr=False)
    _behavior_day: Optional[str] = field(default=None, repr=False)

    def to_kafka(self) -> dict:
        """Возвращает payload без служебных полей '_*'."""
        d = asdict(self)
        d.pop("_anomaly", None)
        d.pop("_behavior_day", None)
        return d


@dataclass
class Authorization:
    authorization_id: str
    transaction_id: str
    client_id: str
    card_id: str
    amount: str
    currency: str
    decision: str              # approved / declined
    reason: Optional[str]
    event_datetime: str


@dataclass
class BehaviorProfile:
    client_id: str
    avg_transaction_amount: float
    usual_countries: List[str]
    usual_cities: List[str]
    usual_hours: List[int]     # [start, end]
    usual_devices: List[str]   # только trusted-устройства (см. profiles.py)
    transactions_per_day: int