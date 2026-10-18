"""«Этос» (ethos.co.il) — городская касса Хайфы: Аудиториум, зал Раппопорт, Центр Кригер,
Хайфский симфонический, клуб BEAT и др.

Страница категории показывает первые события, остальные сайт подгружает порциями через
/includes/events.php (category, start, limit) — так же читаем и мы. Каждое ещё не виденное
событие превращается в короткий «пост» для модели (перевод, категория, оценки).
"""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
import urllib.request
from datetime import date

from .tg import Post
from .webpages import page_text

log = logging.getLogger(__name__)

BASE = "https://www.ethos.co.il"
CHANNEL_TITLE = "Этос (касса Хайфы)"
CARD = re.compile(r"\[(https://www\.ethos\.co\.il/Event/(\d+)[^\]]*)\]")
MAX_PORTIONS = 30


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"user-agent": "Mozilla/5.0 (israel-afisha; personal digest)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def _portion(category: str, start: int, today: date) -> str:
    query = urllib.parse.urlencode({
        "ajax": 1, "category": category, "date": today.strftime("%d.%m.%Y"), "lang": "heb", "start": start, "limit": 7,
    })
    return _get(f"{BASE}/includes/events.php?{query}")


def _details(url: str) -> str:
    """Описание со страницы события (блок event-text), до 1500 символов."""
    try:
        page = _get(url)
    except Exception as err:  # noqa: BLE001
        log.warning("Этос: не удалось открыть %s (%s)", url, err)
        return ""
    i = page.find('class="event-text"')
    if i < 0:
        return ""
    return page_text(page[i : i + 15000], BASE, "")[:1500]


# Признаки русского языка у фильма: кириллица в названии/описании или упоминание на иврите
# («רוסית» — русский язык, в т.ч. «כתוביות ברוסית» — субтитры, «דיבוב לרוסית» — дубляж; «רוסיה» — Россия).
# Одиночное «רוסי» не берём: так пишутся и имена (Росси де Пальма).
RUSSIAN = re.compile(r"[А-Яа-яЁё]{3,}|רוסית|רוסיה")


def is_russian(text: str) -> bool:
    return bool(RUSSIAN.search(text))


def fetch_new(
    categories: dict[str, str], seen: list[str], today: date, russian_only: list[str] | None = None,
) -> tuple[list[Post], list[str]]:
    """Новые события по категориям (код категории → название). seen — id уже отданных модели.
    russian_only — разделы (например, синематека), из которых берём только кино на русском."""
    russian_only = set(russian_only or [])
    cards: dict[str, tuple[str, str, str]] = {}
    codes: dict[str, str] = {}
    for code, label in categories.items():
        found = 0
        for start in range(MAX_PORTIONS):
            try:
                fragment = _portion(code, start, today)
            except Exception as err:  # noqa: BLE001 — сбой сайта не должен ронять весь сбор
                log.warning("Этос /%s: не удалось прочитать порцию %d (%s)", code, start, err)
                break
            text = page_text(fragment, BASE, r"/Event/\d+")
            parts = CARD.split(text)
            # parts: [до первой карточки, url, id, текст карточки, url, id, текст, ...]
            if len(parts) < 4:
                break
            for i in range(1, len(parts) - 2, 3):
                url, eid, body = parts[i], parts[i + 1], parts[i + 2]
                body = "\n".join(x for x in body.split("\n") if x and x not in ("לרכישה ופרטים", "Tickets & Info"))
                cards.setdefault(eid, (url, body, label))
                codes.setdefault(eid, code)
                found += 1
            time.sleep(0.5)  # бережно к сайту
        log.info("Этос /%s (%s): карточек %d", code, label, found)

    new_posts = []
    skipped = 0
    for eid, (url, body, label) in cards.items():
        if eid in seen:
            continue
        about = _details(url)  # описание открываем только для новых событий
        time.sleep(0.3)
        if codes[eid] in russian_only and not is_russian(f"{body}\n{about}"):
            skipped += 1  # не на русском — модели не отдаём, но запоминаем, чтобы не проверять снова
            continue
        new_posts.append(Post(
            channel_id=0, channel_title=CHANNEL_TITLE, channel_username="", message_id=int(eid),
            posted_at="", url=url,
            text=(f"Анонс с сайта городской кассы Хайфы «Этос» (иврит), раздел «{label}». "
                  f"Город — Хайфа, если не указано иное. Дата в формате ДД.ММ.ГГ.\n{body}\n"
                  f"Описание: {about}\nБилеты: {url}"),
        ))
    # Помним только то, что ещё есть на сайте, — прошедшие события выпадают сами.
    log.info("Этос: новых событий %d из %d (отсеяно не на русском: %d)", len(new_posts), len(cards), skipped)
    return new_posts, sorted(cards)
