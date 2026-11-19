# anomalies.py
"""
Сценарии аномалий. Каждая функция модифицирует параметры транзакции
относительно поведения клиента и возвращает dict с переопределёнными полями.

Контракт:
- 'country'/'city' в out — страна/город совершения операции.
  Если они меняются (unusual_country, account_takeover), transactions.py
  обязан подобрать merchant_id из этой страны — иначе merchant.country
  не совпадёт с transaction.country.
- 'force_declines' — сколько первых транзакций пачки сделать declined.
  Финальный статус решается в transactions.py.
"""
import random
from datetime import datetime
from config import COUNTRIES_CITIES
from models import BehaviorProfile


def _pick_foreign_country(
    profile: BehaviorProfile,
    rng: random.Random,
) -> tuple[str, str]:
    """
    Выбирает страну, отличную от страны клиента.

    «Иностранная» — любая страна из COUNTRIES_CITIES, кроме родной.
    Так для клиента из KZ не выберется KZ, а BY останется кандидатом
    (для RU-клиента это правдоподобное направление).
    """
    home = profile.usual_countries[0]
    candidates = [c for c in COUNTRIES_CITIES.keys() if c != home]
    if not candidates:
        candidates = [home]
    c = rng.choice(candidates)
    return c, rng.choice(COUNTRIES_CITIES[c])


def apply_anomaly(
    scenario: str,
    profile: BehaviorProfile,
    base_dt: datetime,
    base_amount: float,
    base_country: str,
    base_city: str,
    base_device: str,
    rng: random.Random,
) -> dict:
    """Возвращает dict с переопределёнными полями транзакции."""
    out = {
        "amount": base_amount,
        "country": base_country,
        "city": base_city,
        "device_id": base_device,
        "event_datetime": base_dt,
        "transaction_type": "purchase",
        "force_declines": 0,
    }

    if scenario == "large_amount":
        # Крупная сумма относительно профиля клиента.
        out["amount"] = base_amount * rng.uniform(8, 25)

    elif scenario == "unusual_country":
        c, city = _pick_foreign_country(profile, rng)
        out["country"], out["city"] = c, city
        out["transaction_type"] = "international"

    elif scenario == "new_device":
        # Анонимное устройство — не из профиля клиента.
        out["device_id"] = f"device-anon-{rng.randint(100000, 999999)}"

    elif scenario == "night_activity":
        # Ночное окно 02:00–05:00. Секунды обнуляем, чтобы не тянуть
        # «дневные» секунды из base_dt.
        out["event_datetime"] = base_dt.replace(
            hour=rng.randint(2, 5),
            minute=rng.randint(0, 59),
            second=0,
        )

    elif scenario == "multiple_declines":
        # Серия отказов. Статус решается в transactions.py по force_declines.
        out["force_declines"] = rng.randint(3, 6)

    elif scenario == "cashout_pattern":
        # Серия снятий/переводов на крупные суммы.
        out["transaction_type"] = rng.choice(["withdrawal", "transfer"])
        out["amount"] = base_amount * rng.uniform(3, 7)

    elif scenario == "account_takeover_pattern":
        # Смена страны + новое устройство + крупный перевод.
        c, city = _pick_foreign_country(profile, rng)
        out["country"], out["city"] = c, city
        out["device_id"] = f"device-anon-{rng.randint(100000, 999999)}"
        out["amount"] = base_amount * rng.uniform(5, 15)
        out["transaction_type"] = "transfer"

    elif scenario == "rapid_transactions":
        # Пачка обрабатывается на уровне transactions.py (batch = 4–8).
        pass

    return out