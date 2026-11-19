# transactions.py
"""
Генерация транзакций и авторизаций.

Ключевые инварианты (проверяются в _validate_tx):
- tx.event_datetime >= card.issued_at (по дню) и <= card.expires_at;
- tx.event_datetime >= device.first_seen_at;
- tx.event_datetime <= end_date + 1 день (запас для ночных сдвигов);
- при аномалиях unusual_country/account_takeover merchant.country == tx.country
  (если мерчанта в стране нет — аномалия откатывается к нормальной);
- multiple_declines ВСЕГДА имеет Authorization (сценарий должен быть виден
  downstream-детектору);
- количество авторизаций стремится к VOLUMES["authorization"]:
  после генерации недостающие добиваются по approved-транзакциям,
  избыточные — обрезаются (детерминированно, через rng).

Серии (batch > 1):
- rapid_transactions: 4–8 транзакций подряд (5–90 сек между ними);
- multiple_declines:  3–6 declined + 1 approved (проверка «карта жива»);
- cashout_pattern:    3–5 withdrawal/transfer на крупные суммы.

Особые случаи:
- device-anon-* (new_device/account_takeover) НЕ входит в devices.jsonl —
  это осознанно: downstream должен делать LEFT JOIN по device_id.
"""
import random
from datetime import datetime, timedelta
from typing import List, Optional

from config import (
    VOLUMES, TX_TYPES, TX_STATUSES, ANOMALY_SCENARIOS,
    CURRENCY_BY_COUNTRY, COUNTRIES_CITIES,
)
from models import (
    Client, Card, Device, Merchant, Transaction, Authorization, BehaviorProfile,
)
from anomalies import apply_anomaly


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _fmt_amount(x: float) -> str:
    return f"{x:.2f}"


def _parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d")


def _parse_dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


def _rand_dt_in_window(rng: random.Random, day: datetime, hours: list[int]) -> datetime:
    """
    Случайный момент внутри дня в пределах [hours[0], hours[1]].

    Если hours[0] > hours[1] — окно считается «через полночь» (например, [23, 8]):
    интерпретируется как [hours[0], 23] ∪ [0, hours[1]].
    """
    h_start, h_end = hours[0], hours[1]
    if h_start <= h_end:
        h = rng.randint(h_start, h_end)
    else:
        if rng.random() < 0.5:
            h = rng.randint(h_start, 23)
        else:
            h = rng.randint(0, h_end)
    return day.replace(
        hour=h,
        minute=rng.randint(0, 59),
        second=rng.randint(0, 59),
    )


def _merchants_by_country(merchants: List[Merchant]) -> dict[str, List[Merchant]]:
    """Индекс мерчантов по стране — чтобы не сканировать 2000 записей на каждый tx."""
    idx: dict[str, List[Merchant]] = {}
    for m in merchants:
        idx.setdefault(m.country, []).append(m)
    return idx


def _pick_reason(scenario: Optional[str], rng: random.Random) -> str:
    """
    Причина отказа авторизации зависит от сценария:
    - multiple_declines → risk_block / card_blocked;
    - large_amount / cashout_pattern → insufficient_funds / risk_block;
    - иначе — случайная.
    """
    if scenario == "multiple_declines":
        return rng.choice(["risk_block", "card_blocked"])
    if scenario in ("large_amount", "cashout_pattern"):
        return rng.choice(["insufficient_funds", "risk_block"])
    return rng.choice(["insufficient_funds", "risk_block", "card_blocked"])


# Маппинг экзотических валют в поддерживаемые CURRENCIES.
# Нужен, чтобы downstream-валидатор currency ∈ CURRENCIES не падал.
_CURRENCY_FALLBACK = {
    "AED": "USD",
    "GEL": "USD",
    "KZT": "RUB",
    "BYN": "RUB",
}


def _currency_for(
    merchant: Merchant,
    is_international: bool,
    rng: random.Random,
) -> str:
    """
    Валюта операции:
    - локальная (не international) — валюта страны мерчанта (fallback RUB);
    - international — валюта мерчанта с шансом 30% валюта карты (RUB).
    Экзотические валюты маппятся в поддерживаемые (см. _CURRENCY_FALLBACK).
    """
    base = CURRENCY_BY_COUNTRY.get(merchant.country, "RUB")
    if is_international and rng.random() < 0.3:
        return "RUB"
    return _CURRENCY_FALLBACK.get(base, base)


# ---------------------------------------------------------------------------
# Валидация инвариантов
# ---------------------------------------------------------------------------

def _validate_tx(
    dt: datetime,
    card_issued: datetime,
    card_expires: datetime,
    device_first: datetime,
    end_date: datetime,
) -> None:
    """
    Проверяет инварианты времени. Бросает AssertionError с диагностикой —
    лучше упасть на генерации, чем отдать downstream битые данные.
    """
    assert dt >= card_issued, (
        f"tx.event_datetime={dt} < card.issued_at={card_issued}"
    )
    assert dt <= card_expires + timedelta(days=1), (
        f"tx.event_datetime={dt} > card.expires_at={card_expires}"
    )
    assert dt >= device_first, (
        f"tx.event_datetime={dt} < device.first_seen_at={device_first}"
    )
    assert dt <= end_date + timedelta(days=1), (
        f"tx.event_datetime={dt} > end_date+1={end_date + timedelta(days=1)}"
    )


# ---------------------------------------------------------------------------
# Основная генерация
# ---------------------------------------------------------------------------

def generate_transactions(
    rng: random.Random,
    clients: List[Client],
    cards: List[Card],
    devices: List[Device],
    merchants: List[Merchant],
    profiles: dict[str, BehaviorProfile],
    cfg,
) -> tuple[List[Transaction], List[Authorization]]:
    """
    Генерирует транзакции и авторизации.

    Алгоритм:
    1. Индексируем карты/устройства по клиенту, мерчантов — по стране.
    2. Для каждого eligible-клиента генерируем n_for_client базовых событий.
    3. Каждое событие — либо нормальная транзакция, либо аномалия с batch > 1.
    4. per_day_limit применяется как «сдвиг в следующий день», НЕ как пропуск —
       общий объём базовых транзакций сохраняется.
    5. После генерации авторизации добиваются/обрезаются до
       VOLUMES["authorization"] (с сохранением всех multiple_declines).
    """
    txns: List[Transaction] = []
    auths: List[Authorization] = []
    auth_counter = 0
    tx_counter = 0

    start = _parse_date(cfg.start_date)
    end = _parse_date(cfg.end_date)
    total_days = (end - start).days + 1

    # --- Индексы ---
    cards_by_client: dict[str, List[Card]] = {}
    for c in cards:
        cards_by_client.setdefault(c.client_id, []).append(c)
    devices_by_client: dict[str, List[Device]] = {}
    for d in devices:
        devices_by_client.setdefault(d.client_id, []).append(d)
    merchants_by_country = _merchants_by_country(merchants)

    eligible = [
        c for c in clients
        if cards_by_client.get(c.client_id) and devices_by_client.get(c.client_id)
    ]
    if not eligible:
        raise ValueError(
            "Нет клиентов с картой и устройством — проверьте VOLUMES/card/device."
        )

    # --- Квоты ---
    # per_client — базовые события на клиента; batch-аномалии добавят сверху.
    base_total = VOLUMES["transaction"]
    per_client = base_total // len(eligible)
    remainder = base_total % len(eligible)

    for idx, client in enumerate(eligible):
        profile = profiles[client.client_id]
        client_cards = cards_by_client[client.client_id]
        client_devices = devices_by_client[client.client_id]

        per_day_limit = max(1, profile.transactions_per_day)
        per_day_count: dict[str, int] = {}

        n_for_client = per_client + (1 if idx < remainder else 0)

        for _ in range(n_for_client):
            card = rng.choice(client_cards)
            device = rng.choice(client_devices)

            card_issued = _parse_date(card.issued_at)
            card_expires = _parse_date(card.expires_at)
            device_first = _parse_dt(device.first_seen_at)

            # День транзакции: не раньше card_issued и device_first,
            # не позже min(end, card_expires).
            day_floor = max(start, card_issued, device_first.replace(
                hour=0, minute=0, second=0, microsecond=0
            ))
            day_ceil = min(end, card_expires)
            if day_ceil < day_floor:
                # Карта/устройство не пересекаются с периодом — пропускаем
                # (крайне редко; такое возможно при неудачных seed).
                continue
            span_days = max(0, (day_ceil - day_floor).days)
            day = day_floor + timedelta(days=rng.randint(0, span_days))

            # Если в этот день уже достигнут лимит — сдвигаем на следующий
            # доступный день (не пропускаем транзакцию!).
            day_key = day.strftime("%Y-%m-%d")
            shift_attempts = 0
            while per_day_count.get(day_key, 0) >= per_day_limit and shift_attempts < span_days + 1:
                day = day + timedelta(days=1)
                if day > day_ceil:
                    day = day_floor
                day_key = day.strftime("%Y-%m-%d")
                shift_attempts += 1
            per_day_count[day_key] = per_day_count.get(day_key, 0) + 1

            base_dt = _rand_dt_in_window(rng, day, profile.usual_hours)
            # Гарантируем, что даже после выбора часа base_dt >= device_first.
            if base_dt < device_first:
                base_dt = device_first

            base_amount = max(
                1.0,
                rng.gauss(profile.avg_transaction_amount,
                          profile.avg_transaction_amount * 0.4),
            )

            merchant = rng.choice(merchants)

            is_anomaly = rng.random() < cfg.anomaly_rate
            anomaly_label: Optional[str] = None

            # Параметры по умолчанию (нормальная транзакция).
            params = {
                "amount": base_amount,
                "country": merchant.country,
                "city": merchant.city,
                "device_id": device.device_id,
                "event_datetime": base_dt,
                "transaction_type": rng.choice(TX_TYPES),
                "force_declines": 0,
                "merchant": merchant,  # объект, чтобы не терять связь
            }

            if is_anomaly:
                anomaly_label = rng.choice(ANOMALY_SCENARIOS)
                params = apply_anomaly(
                    anomaly_label,
                    profile,
                    base_dt,
                    base_amount,
                    merchant.country,
                    merchant.city,
                    device.device_id,
                    rng,
                )
                params["merchant"] = merchant  # сохраняем исходного мерчанта

                # Ссылочная целостность: если страна изменилась — подбираем
                # мерчанта из целевой страны. Если там пусто — откатываем
                # аномалию к нормальной (не создаём битую ссылку).
                if params["country"] != merchant.country:
                    candidates = merchants_by_country.get(params["country"], [])
                    if candidates:
                        params["merchant"] = rng.choice(candidates)
                    else:
                        # Откат: оставляем нормальную транзакцию.
                        anomaly_label = None
                        params["country"] = merchant.country
                        params["city"] = merchant.city
                        params["merchant"] = merchant

            # --- batch: сколько транзакций в серии ---
            batch = 1
            if anomaly_label == "rapid_transactions":
                batch = rng.randint(4, 8)
            elif anomaly_label == "multiple_declines":
                # 3–6 отказов + 1 approved (проверка «карта жива»).
                batch = params["force_declines"] + 1
            elif anomaly_label == "cashout_pattern":
                batch = rng.randint(3, 5)

            for k in range(batch):
                # --- время в серии ---
                if anomaly_label == "rapid_transactions":
                    dt = params["event_datetime"] + timedelta(
                        seconds=rng.randint(5, 90) * k
                    )
                elif anomaly_label in ("multiple_declines", "cashout_pattern"):
                    dt = params["event_datetime"] + timedelta(
                        minutes=rng.randint(1, 15) * k
                    )
                else:
                    dt = params["event_datetime"]

                # Подстраховка: серия не должна выйти за пределы карты/периода.
                if dt > min(card_expires, end) + timedelta(days=1):
                    break
                if dt < device_first:
                    dt = device_first

                _validate_tx(dt, card_issued, card_expires, device_first, end)

                # --- статус ---
                if anomaly_label == "multiple_declines":
                    # Первые force_declines — declined, последняя — approved.
                    status = "declined" if k < params["force_declines"] else "approved"
                else:
                    # Нормальные и прочие аномалии — по общим весам.
                    status = rng.choices(TX_STATUSES, weights=[9, 1])[0]
                    if status == "declined":
                        status = rng.choices(["approved", "declined"], weights=[8, 2])[0]

                # --- сумма (небольшой шум внутри серии) ---
                if batch > 1:
                    amount = params["amount"] * (1 + rng.uniform(-0.05, 0.05))
                else:
                    amount = params["amount"]
                amount = max(1.0, amount)

                # --- мерчант и валюта ---
                tx_merchant: Merchant = params["merchant"]
                is_international = (
                    params["transaction_type"] == "international"
                    or tx_merchant.country != client.country
                )
                currency = _currency_for(tx_merchant, is_international, rng)

                tx = Transaction(
                    transaction_id=f"tx-{tx_counter:07d}",
                    client_id=client.client_id,
                    card_id=card.card_id,
                    merchant_id=tx_merchant.merchant_id,
                    amount=_fmt_amount(amount),
                    currency=currency,
                    transaction_type=params["transaction_type"],
                    country=params["country"],
                    city=params["city"],
                    device_id=params["device_id"],
                    event_datetime=dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    status=status,
                    _anomaly=anomaly_label,
                    _behavior_day=dt.strftime("%Y-%m-%d"),
                )
                txns.append(tx)
                tx_counter += 1

                # --- авторизация ---
                # multiple_declines: ВСЕГДА (иначе сценарий невидим downstream).
                # Остальные: 60%.
                must_auth = (anomaly_label == "multiple_declines")
                if must_auth or rng.random() < 0.6:
                    auths.append(Authorization(
                        authorization_id=f"auth-{auth_counter:07d}",
                        transaction_id=tx.transaction_id,
                        client_id=tx.client_id,
                        card_id=tx.card_id,
                        amount=tx.amount,
                        currency=tx.currency,
                        decision="approved" if status == "approved" else "declined",
                        reason=(
                            None if status == "approved"
                            else _pick_reason(anomaly_label, rng)
                        ),
                        event_datetime=tx.event_datetime,
                    ))
                    auth_counter += 1

    # --- Приведение количества авторизаций к цели ---
    auths = _align_authorizations(auths, txns, auth_counter, rng)

    return txns, auths


# ---------------------------------------------------------------------------
# Выравнивание авторизаций до VOLUMES["authorization"]
# ---------------------------------------------------------------------------

def _align_authorizations(
    auths: List[Authorization],
    txns: List[Transaction],
    auth_counter: int,
    rng: random.Random,
) -> List[Authorization]:
    """
    Приводит число авторизаций к VOLUMES["authorization"].

    Логика:
    - авторизации для multiple_declines НЕ удаляются никогда;
    - если auths < target — добиваем по approved-транзакциям без авторизации;
    - если auths > target — обрезаем «лишние» (без multiple_declines),
      сохраняя детерминированность через rng.

    Возвращает новый список с пересчитанными authorization_id.
    """
    target = VOLUMES["authorization"]
    protected_ids = {
        a.transaction_id for a in auths
        if _is_multiple_declines_auth(a, txns)
    }

    # Обрезка: удаляем «лишние», кроме protected.
    if len(auths) > target:
        removable = [a for a in auths if a.transaction_id not in protected_ids]
        excess = len(auths) - target
        if removable and excess > 0:
            rng.shuffle(removable)
            to_remove = set(id(a) for a in removable[:excess])
            auths = [a for a in auths if id(a) not in to_remove]

    # Добивка: approved-транзакции без авторизации.
    if len(auths) < target:
        have_tx = {a.transaction_id for a in auths}
        candidates = [
            t for t in txns
            if t.status == "approved" and t.transaction_id not in have_tx
        ]
        rng.shuffle(candidates)
        need = target - len(auths)
        for tx in candidates[:need]:
            auths.append(Authorization(
                authorization_id=f"auth-{auth_counter:07d}",
                transaction_id=tx.transaction_id,
                client_id=tx.client_id,
                card_id=tx.card_id,
                amount=tx.amount,
                currency=tx.currency,
                decision="approved",
                reason=None,
                event_datetime=tx.event_datetime,
            ))
            auth_counter += 1

    # Пересчёт authorization_id, чтобы они шли подряд без дыр.
    auths.sort(key=lambda a: a.event_datetime)
    for i, a in enumerate(auths):
        a.authorization_id = f"auth-{i:07d}"

    return auths


def _is_multiple_declines_auth(a: Authorization, txns: List[Transaction]) -> bool:
    """True, если авторизация относится к multiple_declines."""
    # Дешёвый обход: build индекс один раз снаружи было бы быстрее,
    # но список auths обычно мал (≈300k) — приемлемо.
    for t in txns:
        if t.transaction_id == a.transaction_id:
            return t._anomaly == "multiple_declines"
    return False