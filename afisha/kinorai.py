"""Кино на русском от «Кинорая» (kinorai.co.il): только сеансы в Хайфе и любые IMAX.

Страница «Все показы фильмов» содержит таблицы по кинотеатрам: фильм, дата и
время, ссылка на покупку. Для нового фильма модель один раз пишет карточку
(описание, жанр, оценки) по странице фильма; для уже известного программа
только добавляет новые сеансы — оценки при этом не трогаются.
"""

from __future__ import annotations

import html
import logging
import re
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime

from .tg import Post

log = logging.getLogger(__name__)

BASE = "https://kinorai.co.il"
ALL_SHOWS = BASE + "/" + urllib.parse.quote("все-показы-фильмов") + "/"
CHANNEL_TITLE = "КиноРай"
TABLE = re.compile(r'<table class="all_movies">(.*?)</table>', re.S)
THEATER = re.compile(r"<th[^>]*>(.*?)</th>", re.S)
ROW = re.compile(
    r'<div class="name"><a href="([^"]+)">(.*?)</a><div class="date">(.*?)</div>.*?<a href="([^"]+)"[^>]*class="buy"', re.S
)


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"user-agent": "Mozilla/5.0 (israel-afisha; personal digest)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def _text(fragment: str) -> str:
    fragment = re.sub(r"<script.*?</script>|<style.*?</style>", "", fragment, flags=re.S)
    lines = (html.unescape(x).strip() for x in re.sub(r"<[^>]+>", "\n", fragment).split("\n"))
    return "\n".join(x for x in lines if x)


def _wanted(theater: str, film: str, cities: list[str]) -> bool:
    if "imax" in f"{theater} {film}".lower() or "аймакс" in f"{theater} {film}".lower():
        return True
    return any(c.lower() in theater.lower() for c in cities)


def schedule(cities: list[str]) -> dict[str, dict]:
    """Сеансы по фильмам: url фильма → {title, sessions: [{date, time, city, venue}], buy}."""
    page = _get(ALL_SHOWS)
    films: dict[str, dict] = defaultdict(lambda: {"title": "", "sessions": [], "buy": ""})
    for table in TABLE.findall(page):
        m = THEATER.search(table)
        if not m:
            continue
        theater = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
        # «Хот Синема - Хайфа» → кинотеатр и город
        city = theater.rsplit(" - ", 1)[-1].strip() if " - " in theater else ""
        for url, title, when, buy in ROW.findall(table):
            title = html.unescape(title).strip()
            if not _wanted(theater, title, cities):
                continue
            try:
                dt = datetime.strptime(when.strip(), "%d/%m/%Y, %H:%M")
            except ValueError:
                continue
            f = films[url]
            f["title"] = title
            f["buy"] = f["buy"] or html.unescape(buy)
            f["sessions"].append({
                "date": dt.strftime("%Y-%m-%d"), "end_date": "", "time": dt.strftime("%H:%M"),
                "city": city.split()[0] if city.startswith("Хайфа") else city, "venue": theater,
            })
    log.info("КиноРай: фильмов с подходящими сеансами %d", len(films))
    return dict(films)


def film_post(url: str, film: dict) -> Post:
    """Пост для модели о новом фильме: текст страницы фильма и сеансы."""
    try:
        text = _text(_get(url))
        start = text.find("Продолжительность")
        about = text[start : start + 2500] if start >= 0 else text[:2500]
    except Exception as err:  # noqa: BLE001
        log.warning("КиноРай: не удалось открыть страницу фильма %s (%s)", url, err)
        about = ""
    sessions = "\n".join(f"- {s['date']} {s['time']}, {s['venue']}" for s in film["sessions"])
    body = (
        "Фильм в прокате на русском языке (русский дубляж или русский оригинал), прокатчик КиноРай.\n"
        f"Название: {film['title']}\n{about}\n\nСеансы (только эти, другие города не указывать):\n{sessions}\n"
        f"Билеты: {film['buy'] or url}\n"
        "Это одно событие категории «кино»: city — Хайфа, venue — кинотеатр."
    )
    return Post(
        channel_id=0, channel_title=CHANNEL_TITLE, channel_username="",
        message_id=0, posted_at="", url=url, text=body,
    )
