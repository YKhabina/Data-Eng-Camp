# entities.py
"""
Генерация статических сущностей: клиенты, аккаунты, карты, устройства, мерчанты.

Ключевые инварианты:
- client.created_at <= card.issued_at <= card.expires_at;
- client.created_at <= device.first_seen_at <= end_date;
- merchant.mcc ∈ MCC_BY_CATEGORY[merchant.category];
- device.trusted коррелирует с возрастом устройства.
"""
import random
from datetime import datetime, timedelta
from typing import List

from config import (
    VOLUMES, COUNTRIES_CITIES, COUNTRY_WEIGHTS_CLIENT,
    RISK_WEIGHTS_BY_COUNTRY, RISK_ORDER,
    KYC_WEIGHTS_BY_RISK, KYC_ORDER,
    CARD_TYPES, PAYMENT_SYSTEMS, CARD_STATUSES,
    DEVICE_TYPES, OS_BY_TYPE,
    MCC_BY_CATEGORY, RISK_BY_CATEGORY,
)
from models import Client, Account, Card, Device, Merchant

MERCHANT_NAMES = [
    "ООО Ромашка", "ИП Иванов", "Market Plus", "CoffeeHouse",
    "TechStore", "TravelPro", "FuelStation", "PharmaPlus",
]
MERCHANT_CATEGORIES = list(MCC_BY_CATEGORY.keys())


def _rand_date(rng: random.Random, start: str, end: str, fmt="%Y-%m-%d") -> str:
    """Случайная дата в [start, end] включительно. start <= end обязательно."""
    s = datetime.strptime(start, fmt)
    e = datetime.strptime(end, fmt)
    if e < s:
        raise ValueError(f"_rand_date: start={start} > end={end}")
    delta = (e - s).days
    return (s + timedelta(days=rng.randint(0, delta))).strftime(fmt)


def _rand_dt(rng: random.Random, start: str, end: str) -> str:
    """Случайный datetime в [start, end] включительно (ISO, UTC)."""
    s = datetime.fromisoformat(start)
    e = datetime.fromisoformat(end)
    if e < s:
        raise ValueError(f"_rand_dt: start={start} > end={end}")
    sec = int((e - s).total_seconds())
    return (s + timedelta(seconds=rng.randint(0, sec))).strftime("%Y-%m-%dT%H:%M:%SZ")


def gen_clients(rng: random.Random, cfg) -> List[Client]:
    """Клиенты. risk_level зависит от страны, kyc_status — от risk_level."""
    clients = []
    countries = list(COUNTRIES_CITIES.keys())

    for i in range(VOLUMES["client"]):
        country = rng.choices(countries, weights=COUNTRY_WEIGHTS_CLIENT)[0]
        city = rng.choice(COUNTRIES_CITIES[country])

        # risk_level: взвешенный выбор по стране
        weights = RISK_WEIGHTS_BY_COUNTRY[country]
        risk_level = rng.choices(RISK_ORDER, weights=weights)[0]

        # kyc_status: взвешенный выбор по risk_level
        kyc_weights = KYC_WEIGHTS_BY_RISK[risk_level]
        kyc_status = rng.choices(KYC_ORDER, weights=kyc_weights)[0]

        clients.append(Client(
            client_id=f"client-{i:05d}",
            created_at=_rand_date(rng, "2023-01-01", cfg.start_date),
            country=country,
            city=city,
            risk_level=risk_level,
            kyc_status=kyc_status,
        ))
    return clients


def gen_accounts(rng: random.Random, clients: List[Client], cfg) -> List[Account]:
    """Аккаунты. Баланс зависит от типа: savings > wallet."""
    accounts = []
    for i in range(VOLUMES["account"]):
        client = rng.choice(clients)

        account_type = rng.choice(["wallet", "wallet", "wallet", "savings"])
        currency = rng.choice(["RUB", "RUB", "RUB", "USD", "EUR"])

        # savings — накопительный, баланс выше; wallet — операционный.
        if account_type == "savings":
            balance = rng.uniform(10_000, 1_500_000)
        else:
            balance = rng.uniform(0, 300_000)

        # opened_at не раньше создания клиента и не позже конца периода.
        opened_at = _rand_date(rng, client.created_at, cfg.end_date)

        accounts.append(Account(
            account_id=f"acc-{i:06d}",
            client_id=client.client_id,
            account_type=account_type,
            currency=currency,
            balance=f"{balance:.2f}",
            opened_at=opened_at,
            status=rng.choices(["active", "frozen", "closed"], weights=[90, 7, 3])[0],
        ))
    return accounts


def gen_cards(rng: random.Random, clients: List[Client], cfg) -> List[Card]:
    """Карты. issued_at >= client.created_at; expires_at = issued_at + 4 года."""
    cards = []
    for i in range(VOLUMES["card"]):
        client = rng.choice(clients)
        issued = _rand_date(rng, client.created_at, cfg.end_date)
        issued_dt = datetime.strptime(issued, "%Y-%m-%d")
        expires = (issued_dt + timedelta(days=365 * 4)).strftime("%Y-%m-%d")

        cards.append(Card(
            card_id=f"card-{i:05d}",
            client_id=client.client_id,
            card_type=rng.choice(CARD_TYPES),
            payment_system=rng.choice(PAYMENT_SYSTEMS),
            status=rng.choice(CARD_STATUSES),
            issued_at=issued,
            expires_at=expires,
        ))
    return cards


def gen_devices(rng: random.Random, clients: List[Client], cfg) -> List[Device]:
    """
    Устройства. trusted зависит от возраста:
    - device старше 180 дней → 90% trusted;
    - 30–180 дней → 70%;
    - моложе 30 дней → 30% (новое устройство ещё не «заслужило» доверие).
    """
    devices = []
    start_dt = datetime.strptime(cfg.start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(cfg.end_date, "%Y-%m-%d")

    for i in range(VOLUMES["device"]):
        client = rng.choice(clients)
        dtype = rng.choice(DEVICE_TYPES)

        first_seen = _rand_dt(
            rng,
            f"{client.created_at}T00:00:00",
            f"{cfg.end_date}T23:59:59",
        )
        first_seen_dt = datetime.strptime(first_seen, "%Y-%m-%dT%H:%M:%SZ")
        age_days = (end_dt - first_seen_dt).days

        if age_days > 180:
            trust_prob = 0.90
        elif age_days > 30:
            trust_prob = 0.70
        else:
            trust_prob = 0.30

        devices.append(Device(
            device_id=f"device-{i:05d}",
            client_id=client.client_id,
            device_type=dtype,
            os=rng.choice(OS_BY_TYPE[dtype]),
            trusted=rng.random() < trust_prob,
            first_seen_at=first_seen,
        ))
    return devices


def gen_merchants(rng: random.Random, cfg) -> List[Merchant]:
    """
    Мерчанты. MCC и risk_level согласованы с category:
    - mcc ∈ MCC_BY_CATEGORY[category];
    - risk_level ∈ RISK_BY_CATEGORY[category].
    """
    merchants = []
    countries = list(COUNTRIES_CITIES.keys())
    # Веса стран для мерчантов: чуть больше иностранных, чем у клиентов.
    merchant_country_weights = [75, 5, 5, 8, 3, 2, 2]

    for i in range(VOLUMES["merchant"]):
        country = rng.choices(countries, weights=merchant_country_weights)[0]
        category = rng.choice(MERCHANT_CATEGORIES)

        merchants.append(Merchant(
            merchant_id=f"merchant-{i:04d}",
            name=f"{rng.choice(MERCHANT_NAMES)} #{i}",
            category=category,
            mcc=rng.choice(MCC_BY_CATEGORY[category]),
            country=country,
            city=rng.choice(COUNTRIES_CITIES[country]),
            risk_level=rng.choice(RISK_BY_CATEGORY[category]),
        ))
    return merchants