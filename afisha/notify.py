"""Утреннее личное сообщение владельцу от бота.

Каждый день — только новое со вчера: 🔔 любимые артисты и до N лучших по
личной оценке. По четвергам — вдобавок «Мой топ на выходные».
Под каждым событием кнопки 👍 / ✕ / 👎 (их обрабатывает сервер оценок).
Если нового нет и сегодня не четверг — ничего не отправляем.
"""

from __future__ import annotations

import html
import json
import logging
import urllib.request
from datetime import date, timedelta

log = logging.getLogger(__name__)

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
CAT_ICON = {
    "концерт": "🎵", "театр": "🎭", "стендап": "🎤", "лекция": "🗣", "выставка": "🖼",
    "фестиваль": "🎪", "экскурсия": "🥾", "кино": "🎬", "детям": "🧸", "литература": "📚",
    "вечеринка": "🪩", "мастер-класс": "✂️", "игра": "🧩", "другое": "✨",
}


def _end(s: dict) -> str:
    return s.get("end_date") or s["date"]


def _human(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {MONTHS[d.month - 1]}, {WEEKDAYS[d.weekday()]}"


def _best_session(e: dict, start: str, end: str, home: str) -> dict | None:
    sessions = [s for s in e["sessions"] if _end(s) >= start and s["date"] <= end]
    if not sessions:
        return None
    sessions.sort(key=lambda s: (s["date"], s.get("time", "")))
    return next((s for s in sessions if s.get("region") == home), sessions[0])


def _score(e: dict, s: dict, profile: dict) -> int:
    score = e.get("personal", e["interest"])
    if s.get("region") != profile.get("home_region"):
        major = e["interest"] >= profile.get("major_interest", 8)
        score -= profile.get("far_penalty_if_major", 1) if major else profile.get("far_penalty", 3)
    return score


def _line(n: int, e: dict, s: dict) -> str:
    when = f"до {_human(s['end_date'])}" if s.get("end_date") and s["end_date"] != s["date"] else _human(s["date"])
    if s.get("time"):
        when += f", {s['time']}"
    place = ", ".join(x for x in (s.get("city"), s.get("venue")) if x and x != "Неизвестно")
    price = "бесплатно" if e.get("price_min") == 0 else (e.get("price_text") or "")
    fav = f"🔔 <b>{html.escape(e['favorite_artist'])}</b>\n" if e.get("favorite_artist") else ""
    meta = " · ".join(x for x in (when, place, price) if x)
    return (
        f"{fav}{n}. {CAT_ICON.get(e['category'], '')} <b>{html.escape(e['title'])}</b>\n"
        f"{html.escape(meta)}\n<i>{html.escape(e['summary'])}</i>"
    )


def build(events: list[dict], feedback: dict, profile: dict, today: date, cfg: dict) -> tuple[str, list] | None:
    """Текст сообщения и кнопки; None — если отправлять нечего."""
    t = today.isoformat()
    voted = {x["id"] for kind in ("likes", "dislikes", "hidden") for x in feedback.get(kind, [])}
    horizon = (today + timedelta(days=cfg.get("new_horizon_days", 120))).isoformat()
    home = profile.get("home_region", "")
    min_score = cfg.get("min_score", 5)

    # Новое со вчера: любимые артисты — все, остальное — лучшее по личной оценке.
    fresh = []
    for e in events:
        if e.get("first_seen") != t or e["id"] in voted:
            continue
        s = _best_session(e, t, "9999-12-31" if e.get("favorite_artist") else horizon, home)
        if s:
            fresh.append((e, s, _score(e, s, profile)))
    favs = [x for x in fresh if x[0].get("favorite_artist")]
    others = sorted((x for x in fresh if not x[0].get("favorite_artist") and x[2] >= min_score),
                    key=lambda x: (-x[2], x[1]["date"]))[: cfg.get("new_count", 5)]

    # По четвергам — топ на ближайшие пятницу и субботу.
    weekend = []
    if today.weekday() == 3:
        fri, sat = (today + timedelta(days=1)).isoformat(), (today + timedelta(days=2)).isoformat()
        shown = {x[0]["id"] for x in favs + others}
        for e in events:
            if e["id"] in voted or e["id"] in shown:
                continue
            s = _best_session(e, fri, sat, home)
            if s:
                weekend.append((e, s, _score(e, s, profile)))
        weekend = sorted((x for x in weekend if x[2] >= cfg.get("weekend_min_score", 3)), key=lambda x: -x[2])[: cfg.get("weekend_count", 7)]

    if not (favs or others or weekend):
        return None

    parts, buttons, n = [], [], 0
    def add(block_title: str, items: list) -> None:
        nonlocal n
        if not items:
            return
        parts.append(f"<b>{block_title}</b>")
        for e, s, _ in items:
            n += 1
            parts.append(_line(n, e, s))
            buttons.append([
                {"text": f"{n} 👍", "callback_data": f"v|up|{e['id']}"},
                {"text": "✕", "callback_data": f"v|hide|{e['id']}"},
                {"text": "👎", "callback_data": f"v|down|{e['id']}"},
            ])
    add("🔔 Любимые артисты", favs)
    add("Новое для вас", others)
    add("Мой топ на выходные", weekend)
    buttons.append([{"text": "Открыть афишу", "web_app": {"url": cfg["site_url"]}}])
    footer = "\n\n<i>👍 — интересно · ✕ — скрыть · 👎 — не моё</i>"
    text = "\n\n".join(parts) + footer
    if len(text) > 4000:  # предел Telegram — 4096 символов; без описаний сообщение короче
        text = "\n\n".join(p.split("\n<i>")[0] for p in parts) + footer
    return text[:4096], buttons


def send(bot_token: str, chat_id: int | str, text: str, buttons: list) -> None:
    payload = {
        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": True, "reply_markup": {"inline_keyboard": buttons},
    }
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=json.dumps(payload).encode(), headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        result = json.loads(r.read())
    if not result.get("ok"):
        raise RuntimeError(result)
