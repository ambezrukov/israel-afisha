"""Хранилище событий: склейка дублей, регионы, удаление прошедшего."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from difflib import SequenceMatcher
from pathlib import Path

REGIONS = {
    "Север": [
        "Хайфа", "Крайот", "Кирьят-Ям", "Кирьят-Моцкин", "Кирьят-Бялик",
        "Кирьят-Ата", "Нешер", "Тират-Кармель", "Нагария", "Акко", "Кармиэль",
        "Цфат", "Тверия", "Назарет", "Нацрат-Илит", "Ноф-ха-Галиль", "Афула",
        "Йокнеам", "Зихрон-Яаков", "Кейсария", "Хадера", "Бинямина",
        "Кирьят-Шмона", "Маалот", "Кибуц Кабри", "Шломи", "Тель-Хай",
        "Пардес-Хана", "Ор-Акива", "Рош-Пина", "Метула", "Кацрин", "Бейт-Шеан",
        "Кибуц Ягур", "Мегиддо", "Атлит", "Фрейдис", "Даллият-эль-Кармель", "Исфия",
    ],
    "Центр": [
        "Тель-Авив", "Яффо", "Рамат-Ган", "Гиватаим", "Бней-Брак", "Холон",
        "Бат-Ям", "Петах-Тиква", "Ришон-ле-Цион", "Реховот", "Нес-Циона",
        "Раанана", "Герцлия", "Кфар-Саба", "Ход-ха-Шарон", "Нетания",
        "Рош-ха-Аин", "Модиин", "Лод", "Рамла", "Явне", "Гедера", "Ор-Йегуда",
        "Кирьят-Оно", "Рамат-ха-Шарон", "Кибуц Гиват-Бренер", "Ганей-Тиква",
        "Кфар-Йона", "Оранит", "Гедерот", "Кибуц Наан", "Дром ха-Шарон",
        "Эвен-Йегуда", "Кадима", "Шохам", "Ирус", "Кирьят-Экрон", "Мазкерет-Батья",
    ],
    "Иерусалим": [
        "Иерусалим", "Бейт-Шемеш", "Мевасерет-Цион", "Маале-Адумим", "Бейт-Джамаль",
        "Абу-Гош", "Цур-Хадасса",
    ],
    "Юг": [
        "Ашдод", "Ашкелон", "Беэр-Шева", "Сдерот", "Нетивот", "Офаким",
        "Кирьят-Гат", "Димона", "Арад", "Эйлат", "Мицпе-Рамон",
    ],
    "Онлайн": ["Онлайн"],
}
CITY_TO_REGION = {city: region for region, cities in REGIONS.items() for city in cities}

# Крайоты в постах пишут по-разному; сводим к одному названию.
CITY_ALIASES = {
    "крайот": "Крайот",
    "кирьят-моцкин": "Крайот",
    "кирьят-бялик": "Крайот",
    "кирьят-ата": "Крайот",
    "кирьят-ям": "Крайот",
    "тель авив": "Тель-Авив",
    "тель-авив-яффо": "Тель-Авив",
    "яффо": "Тель-Авив",
    "ришон ле-цион": "Ришон-ле-Цион",
    "ришон-ле-цион": "Ришон-ле-Цион",
    "ришон": "Ришон-ле-Цион",
    "петах тиква": "Петах-Тиква",
    "беер-шева": "Беэр-Шева",
    "беэр шева": "Беэр-Шева",
    # Районы Хайфы
    "кирьят-хаим": "Хайфа",
    "кирьят хаим": "Хайфа",
    "бат-галим": "Хайфа",
    "адар": "Хайфа",
    # Разные написания
    "наария": "Нагария",
    "нагария": "Нагария",
    "кфар-сава": "Кфар-Саба",
    "кфар сава": "Кфар-Саба",
    "рамат ха-шарон": "Рамат-ха-Шарон",
    "рамат а-шарон": "Рамат-ха-Шарон",
    "гиват-бренер": "Кибуц Гиват-Бренер",
    "кибуц гиват бренер": "Кибуц Гиват-Бренер",
    "дром а-шарон": "Дром ха-Шарон",
    "гдерот": "Гедерот",
    "наан": "Кибуц Наан",
    "кабри": "Кибуц Кабри",
    "зихрон яаков": "Зихрон-Яаков",
    "зихрон-яков": "Зихрон-Яаков",
    "ганей тиква": "Ганей-Тиква",
    "ход ха-шарон": "Ход-ха-Шарон",
    "рош ха-аин": "Рош-ха-Аин",
    "назарет-илит": "Ноф-ха-Галиль",
    "ришон лецион": "Ришон-ле-Цион",
    "кирьят бьялик": "Крайот",
    "кирьят бялик": "Крайот",
    "бат ям": "Бат-Ям",
    "петах тиква": "Петах-Тиква",
    "кфар саба": "Кфар-Саба",
    "беэр шева": "Беэр-Шева",
    "аялон рамат ган": "Рамат-Ган",
    "нацрат-илит": "Ноф-ха-Галиль",
}


def is_excluded(event: dict, rules: list[dict]) -> bool:
    """Подходит ли событие под правило «не показывать» из профиля."""
    for rule in rules or []:
        if all(not rule.get(k) or event.get(k) == rule[k] for k in ("category", "language")):
            return True
    return False


def normalize_city(city: str) -> str:
    city = city.strip()
    return CITY_ALIASES.get(city.lower(), city) or "Неизвестно"


def region_of(city: str) -> str:
    return CITY_TO_REGION.get(city, "Другое" if city not in ("Неизвестно", "") else "Неизвестно")


def _norm_title(title: str) -> str:
    title = title.lower().replace("ё", "е")
    title = re.sub(r"[«»\"'“”„()\[\]!?.,:;—–-]", " ", title)
    return re.sub(r"\s+", " ", title).strip()


# Слова, общие для разных событий одного организатора; по ним события не сравниваем.
GENERIC_WORDS = {
    "театр", "театра", "спектакль", "концерт", "оркестр", "оркестра", "ансамбль", "фестиваль",
    "выставка", "серия", "лекция", "встреча", "шоу", "для", "детей", "детский", "израильский",
    "израильская", "иерусалимский", "камерата", "барокко", "опера", "мини", "вечер", "программа",
}


def _words(title: str) -> set[str]:
    return {w for w in title.split() if len(w) >= 3 and w not in GENERIC_WORDS}


def _similar(a: str, b: str) -> bool:
    a, b = _norm_title(a), _norm_title(b)
    if not a or not b:
        return False
    if a in b or b in a:
        return min(len(a), len(b)) >= 8
    # Одно название — «расширенная» версия другого: почти все значимые слова короткого есть в длинном.
    wa, wb = _words(a), _words(b)
    if min(len(wa), len(wb)) >= 2 and len(wa & wb) / min(len(wa), len(wb)) >= 0.75:
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.72


def _event_id(event: dict) -> str:
    first = min((s["date"] for s in event["sessions"]), default="")
    raw = f"{_norm_title(event['title'])}|{first}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _valid_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except (TypeError, ValueError):
        return False


def _clean(event: dict) -> dict | None:
    sessions = []
    seen = set()
    for s in event.get("sessions", []):
        if not _valid_date(s.get("date", "")):
            continue
        if s.get("end_date") and not _valid_date(s["end_date"]):
            s["end_date"] = ""
        s["city"] = normalize_city(s.get("city", ""))
        s["region"] = region_of(s["city"])
        key = (s["date"], s.get("time", ""), s["city"])
        if key in seen:
            continue
        seen.add(key)
        sessions.append(s)
    # Сеанс без времени лишний, если в тот же день и в том же городе есть сеанс со временем.
    timed = {(s["date"], s["city"]) for s in sessions if s.get("time")}
    sessions = [s for s in sessions if s.get("time") or (s["date"], s["city"]) not in timed]
    if not sessions or not event.get("title"):
        return None
    event["sessions"] = sorted(sessions, key=lambda s: (s["date"], s.get("time", "")))
    event["interest"] = _clamp(event.get("interest", 5))
    event["personal"] = _clamp(event.get("personal", event["interest"]))
    event["favorite_artist"] = event.get("favorite_artist", "") or ""
    return event


def _clamp(value) -> int:
    try:
        return max(1, min(10, int(value)))
    except (TypeError, ValueError):
        return 5


def _session_dates(event: dict) -> set[str]:
    return {s["date"] for s in event["sessions"]}


def _overlaps(a: dict, b: dict) -> bool:
    """Пересекаются ли события по датам (с учётом периодов выставок)."""
    if _session_dates(a) & _session_dates(b):
        return True
    for x in a["sessions"]:
        for y in b["sessions"]:
            x_end = x.get("end_date") or x["date"]
            y_end = y.get("end_date") or y["date"]
            if x["date"] <= y_end and y["date"] <= x_end:
                return True
    return False


def _merge_into(target: dict, new: dict) -> None:
    urls = {s["url"] for s in target["sources"]}
    target["sources"].extend(s for s in new["sources"] if s["url"] not in urls)
    for s in new["sessions"]:
        # Тот же день и город: сеанс без времени считаем тем же самым и дополняем.
        same = [
            t for t in target["sessions"]
            if t["date"] == s["date"] and t["city"] == s["city"]
            and (t.get("time", "") == s.get("time", "") or not t.get("time") or not s.get("time"))
        ]
        if not same:
            target["sessions"].append(s)
            continue
        for t in same:
            for field in ("time", "venue", "end_date"):
                if not t.get(field) and s.get(field):
                    t[field] = s[field]
    target["sessions"].sort(key=lambda s: (s["date"], s.get("time", "")))
    target["interest"] = max(target["interest"], new["interest"])
    target["personal"] = max(target.get("personal", 0), new.get("personal", 0))
    if not target.get("favorite_artist") and new.get("favorite_artist"):
        target["favorite_artist"] = new["favorite_artist"]
    for field in ("ticket_url", "price_text"):
        if not target.get(field) and new.get(field):
            target[field] = new[field]
    if target.get("price_min", -1) < 0 <= new.get("price_min", -1):
        target["price_min"] = new["price_min"]
    for field in ("language", "kids"):
        if target.get(field) == "unknown" and new.get(field) != "unknown":
            target[field] = new[field]
    # Если хотя бы один источник надёжный (текст, а не картинка), проверка не нужна.
    target["needs_check"] = target.get("needs_check", False) and new.get("needs_check", False)


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.events: list[dict] = []
        if path.exists():
            self.events = json.loads(path.read_text(encoding="utf-8")).get("events", [])

    def add(self, new_events: list[dict], today: date) -> tuple[int, int]:
        """Добавляет события. Возвращает (новых, склеенных с уже известными)."""
        added = merged = 0
        by_id = {e["id"]: e for e in self.events}
        for raw in new_events:
            same_as = raw.pop("same_as", "")
            event = _clean(raw)
            if not event:
                continue
            # Сначала доверяем модели (она видела список известных событий),
            # затем — сравнению названий и дат.
            match = by_id.get(same_as) or next(
                (e for e in self.events if _similar(e["title"], event["title"]) and _overlaps(e, event)),
                None,
            )
            if match:
                _merge_into(match, event)
                match["updated"] = today.isoformat()
                merged += 1
            else:
                event["id"] = _event_id(event)
                event["first_seen"] = today.isoformat()
                event["updated"] = today.isoformat()
                self.events.append(event)
                by_id[event["id"]] = event
                added += 1
        return added, merged

    def upcoming(self, today: date) -> list[dict]:
        t = today.isoformat()
        return [e for e in self.events if any((s.get("end_date") or s["date"]) >= t for s in e["sessions"])]

    def renormalize(self) -> None:
        """Повторно применяет справочник городов к уже сохранённым событиям."""
        for e in self.events:
            for s in e["sessions"]:
                s["city"] = normalize_city(s["city"])
                s["region"] = region_of(s["city"])
            timed = {(s["date"], s["city"]) for s in e["sessions"] if s.get("time")}
            e["sessions"] = [s for s in e["sessions"] if s.get("time") or (s["date"], s["city"]) not in timed]

    def apply_scores(self, scores: list[dict]) -> tuple[int, int]:
        """Применяет новые оценки; склеивает отмеченные дубли. Возвращает (оценено, склеено)."""
        by_id = {e["id"]: e for e in self.events}
        merged_into: dict[str, str] = {}
        scored = 0
        for sc in scores:
            e = by_id.get(sc["id"])
            if not e:
                continue
            e["interest"] = _clamp(sc["interest"])
            e["interest_reason"] = sc["interest_reason"]
            e["personal"] = _clamp(sc["personal"])
            e["personal_reason"] = sc["personal_reason"]
            e["favorite_artist"] = sc["favorite_artist"]
            scored += 1
        merged = 0
        for sc in scores:
            dup, target_id = sc["id"], sc.get("duplicate_of", "")
            while target_id in merged_into:  # цель сама уже склеена с другим событием
                target_id = merged_into[target_id]
            if not target_id or target_id == dup or dup in merged_into or target_id not in by_id or dup not in by_id:
                continue
            _merge_into(by_id[target_id], by_id[dup])
            merged_into[dup] = target_id
            merged += 1
        self.events = [e for e in self.events if e["id"] not in merged_into]
        return scored, merged

    def prune(self, today: date, keep_past_days: int, horizon_days: int) -> int:
        """Удаляет прошедшие события и сеансы, а также слишком далёкие."""
        cutoff = (today - timedelta(days=keep_past_days)).isoformat()
        horizon = (today + timedelta(days=horizon_days)).isoformat()
        kept = []
        for e in self.events:
            e["sessions"] = [
                s for s in e["sessions"]
                if (s.get("end_date") or s["date"]) >= cutoff and s["date"] <= horizon
            ]
            if e["sessions"]:
                kept.append(e)
        removed = len(self.events) - len(kept)
        self.events = kept
        return removed

    def save(self, generated_at: str, profile: dict | None = None) -> None:
        self.events.sort(key=lambda e: (e["sessions"][0]["date"], e["title"]))
        payload = {"generated_at": generated_at, "count": len(self.events)}
        if profile:
            # Странице нужны только правила географии для «Моего топа» и исключения.
            payload["profile"] = {
                k: profile[k] for k in ("home_region", "far_penalty", "far_penalty_if_major", "major_interest", "exclude")
                if k in profile
            }
        payload["events"] = self.events
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
