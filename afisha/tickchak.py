"""Чтение афиши с сайта Tickchak LIVE (live.tickchak.co.il).

Страницы сайта содержат данные о событиях в встроенном JSON (__NEXT_DATA__):
у страниц городов, районов и площадок — data.event.all, у страниц артистов —
artist.events.mainEvents. Каждое новое событие превращается в «пост» с
готовыми полями, который дальше разбирает модель (перевод, категория, оценки).
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from .tg import Post

log = logging.getLogger(__name__)

BASE = "https://live.tickchak.co.il"
CHANNEL_TITLE = "Tickchak LIVE"
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _fetch_state(path: str) -> dict:
    req = urllib.request.Request(
        f"{BASE}/{path.lstrip('/')}",
        headers={"user-agent": "Mozilla/5.0 (israel-afisha; personal digest)", "accept-language": "he,en;q=0.8"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8", "replace")
    m = NEXT_DATA.search(html)
    if not m:
        raise ValueError("на странице нет данных __NEXT_DATA__")
    return json.loads(m.group(1))["props"]["pageProps"]["initialState"]


def _page_events(state: dict) -> list[dict]:
    events = list(((state.get("data") or {}).get("event") or {}).get("all", {}).values())
    artist_events = ((state.get("artist") or {}).get("events") or {})
    for value in artist_events.values():
        if isinstance(value, list):
            events += [e for e in value if isinstance(e, dict)]
    return events


def _to_post(e: dict, tz: ZoneInfo, favorite: str) -> Post:
    start = datetime.fromtimestamp(e["date"], tz)
    venue = e.get("venue") or {}
    city = (venue.get("city") or {}).get("title", "")
    area = (venue.get("area") or {}).get("title", "")
    artists = ", ".join(
        f"{a.get('title', '')} ({a['titleEn']})" if a.get("titleEn") else a.get("title", "")
        for a in e.get("artists") or []
    )
    categories = ", ".join(
        c.get("title", "")
        for a in e.get("artists") or []
        for c in ((a.get("categories") or {}).get("all") or [])
    )
    price = ""
    if e.get("minPrice"):
        price = f"{float(e['minPrice']):.0f}–{float(e.get('maxPrice') or e['minPrice']):.0f} ₪"
    page = BASE + (e.get("url") or "")
    lines = [
        "Анонс с сайта касс Tickchak LIVE (иврит).",
        f"Название: {e.get('title', '')}",
        f"Дата и время: {start:%Y-%m-%d %H:%M}",
        f"Площадка: {venue.get('title', '')}",
        f"Город: {city}" + (f" ({area})" if area else ""),
        f"Артисты: {artists}" if artists else "",
        f"Жанр на сайте: {categories}" if categories else "",
        f"Цена: {price}" if price else "",
        "Билеты почти распроданы." if e.get("lastTickets") else "",
        "Билеты распроданы." if e.get("soldOut") else "",
        f"Описание: {e.get('metaDescription', '')}" if e.get("metaDescription") else "",
        f"Билеты: {e.get('pageUrl') or page}",
        f"Любимый артист владельца: {favorite}" if favorite else "",
    ]
    return Post(
        channel_id=0,
        channel_title=CHANNEL_TITLE,
        channel_username="",
        message_id=int(e.get("id") or 0),
        posted_at="",
        url=page,
        text="\n".join(x for x in lines if x),
    )


def fetch_new(pages: list[str], artists: dict[str, str], seen: dict[str, int], tz: ZoneInfo) -> tuple[list[Post], dict[str, int]]:
    """Новые будущие события со страниц сайта. seen: entityId → дата события (unix)."""
    now = time.time()
    found: dict[str, tuple[dict, str]] = {}
    targets = [(p, "") for p in pages] + [(slug, name) for slug, name in artists.items()]
    for path, favorite in targets:
        try:
            events = _page_events(_fetch_state(path))
        except Exception as err:  # noqa: BLE001 — один сбойный адрес не должен ронять весь сбор
            log.warning("Tickchak /%s: не удалось прочитать (%s)", path, err)
            continue
        fresh = 0
        for e in events:
            eid = e.get("entityId")
            if not eid or not e.get("date") or e["date"] < now or e.get("isPastEvent"):
                continue
            if eid not in found or favorite:
                found[eid] = (e, favorite or found.get(eid, (None, ""))[1])
            fresh += 1
        log.info("Tickchak /%s: будущих событий %d", path, fresh)
        time.sleep(1)  # бережно к сайту

    new_posts = [_to_post(e, tz, fav) for eid, (e, fav) in found.items() if eid not in seen]
    updated = {eid: d for eid, d in seen.items() if d >= now}  # забываем прошедшие
    updated.update({eid: e["date"] for eid, (e, _) in found.items()})
    log.info("Tickchak: новых событий %d", len(new_posts))
    return new_posts, updated
