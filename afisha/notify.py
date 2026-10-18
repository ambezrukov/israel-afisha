"""Вечернее личное сообщение владельцу от бота (после сбора, около 20:00).

Каждый день — только новое со вчера: 🔔 любимые артисты и до N лучших по
личной оценке. По четвергам — вдобавок «Мой топ на выходные».
Каждое событие — отдельное беззвучное сообщение со своими кнопками
«Интересно / Не сейчас / Не моё» (их обрабатывает сервер оценок).
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


def _line(e: dict, s: dict) -> str:
    when = f"до {_human(s['end_date'])}" if s.get("end_date") and s["end_date"] != s["date"] else _human(s["date"])
    if s.get("time"):
        when += f", {s['time']}"
    place = ", ".join(x for x in (s.get("city"), s.get("venue")) if x and x != "Неизвестно")
    price = "бесплатно" if e.get("price_min") == 0 else (e.get("price_text") or "")
    fav = f"🔔 <b>{html.escape(e['favorite_artist'])}</b>\n" if e.get("favorite_artist") else ""
    meta = " · ".join(x for x in (when, place, price) if x)
    return (
        f"{fav}{CAT_ICON.get(e['category'], '')} <b>{html.escape(e['title'])}</b>\n"
        f"{html.escape(meta)}\n\n{html.escape(e['summary'])}"
    )


def build(events: list[dict], feedback: dict, profile: dict, today: date, cfg: dict) -> list[tuple[str, list, bool]] | None:
    """Сообщения (текст, кнопки, беззвучно): заголовок и по одному на событие; None — если отправлять нечего."""
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

    messages: list[tuple[str, list, bool]] = []
    head = []
    if favs:
        head.append(f"🔔 любимые артисты: {len(favs)}")
    if others:
        head.append(f"новое для вас: {len(others)}")
    if weekend:
        head.append(f"на выходные: {len(weekend)}")
    messages.append((
        "<b>Добрый вечер! Новое в афише</b>\n" + " · ".join(head),
        [[{"text": "Открыть афишу", "web_app": {"url": cfg["site_url"]}}]],
        False,  # звук только у заголовка
    ))
    # У любимых артистов своя строка «🔔 Имя», отдельная подпись не нужна.
    for label, items in (("", favs), ("✨ Новое для вас", others), ("📅 На выходные", weekend)):
        for e, s_, _ in items:
            buttons = [[
                {"text": "👍 Интересно", "callback_data": f"v|up|{e['id']}"},
                {"text": "🙈 Не сейчас", "callback_data": f"v|hide|{e['id']}"},
                {"text": "👎 Не моё", "callback_data": f"v|down|{e['id']}"},
            ]]
            link = e.get("ticket_url") or (e.get("sources") or [{}])[0].get("url", "")
            if link.startswith("http"):
                buttons.append([{"text": "Билеты и подробности", "url": link}])
            prefix = f"<i>{label}</i>\n" if label else ""
            messages.append((prefix + _line(e, s_), buttons, True))
    return messages


def send_all(bot_token: str, chat_id: int | str, messages: list[tuple[str, list, bool]]) -> None:
    for text, buttons, silent in messages:
        send(bot_token, chat_id, text, buttons, silent)


def send(bot_token: str, chat_id: int | str, text: str, buttons: list, silent: bool = False) -> None:
    payload = {
        "chat_id": chat_id, "text": text[:4096], "parse_mode": "HTML", "disable_notification": silent,
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
