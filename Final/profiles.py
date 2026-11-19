# profiles.py
"""
Построение поведенческого профиля клиента — эталона «нормы» для детектора.

Профиль строится по:
- стране/городу клиента;
- risk_level (влияет на средний чек);
- trusted-устройствам клиента.
"""
import random
from config import RISK_LEVELS  # noqa: F401 (оставлено для обратной совместимости)
from models import BehaviorProfile, Client, Device


def build_profile(
    client: Client,
    devices: list[Device],
    rng: random.Random,
) -> BehaviorProfile:
    """
    Строит профиль клиента.

    usual_devices включает только trusted-устройства: недоверенные устройства
    не должны считаться «нормой» — иначе аномалия new_device теряет смысл.
    """
    usual_country = client.country
    usual_city = client.city

    # Только trusted-устройства формируют «норму».
    usual_devices = [d.device_id for d in devices if d.trusted]
    if not usual_devices:
        usual_devices = ["device-unknown"]

    # Средний чек зависит от risk_level.
    if client.risk_level == "low":
        base = rng.uniform(800, 2_000)
    elif client.risk_level == "high":
        base = rng.uniform(8_000, 40_000)
    else:
        base = rng.uniform(1_500, 6_000)

    # Обычные часы: у большинства 8–23, у 15% клиентов — расширенные (0–23).
    if rng.random() < 0.15:
        hours = [0, 23]
    else:
        hours = [8, 23]

    return BehaviorProfile(
        client_id=client.client_id,
        avg_transaction_amount=round(base, 2),
        usual_countries=[usual_country],
        usual_cities=[usual_city],
        usual_hours=hours,
        usual_devices=usual_devices,
        transactions_per_day=rng.randint(1, 12),
    )