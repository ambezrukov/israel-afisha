"""Личная оценка событий моделью-классификатором Jev (TypeSafe AI).

Jev не пишет текст, а ставит оценку по шкале с уверенностью. В сравнении на 198
оценках владельца она угадывала его вкус не хуже Gemini и Claude Haiku, а стоит
доли цента и работает за секунды. Ключ — TYPESAFE_API_KEY.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

LEVELS = [f"{n} — " + d for n, d in zip(range(1, 11), [
    "совсем не интересно владельцу", "почти не интересно", "скорее не интересно", "так себе",
    "нейтрально", "умеренно интересно", "интересно", "очень интересно", "обязательно стоит пойти",
    "любимый артист или событие мечты",
])]
INSTRUCTIONS = (
    "How interesting is this cultural event to the owner of the digest, judging by the owner's profile, "
    "stated tastes and the examples they liked (👍) and disliked (👎)? Ignore geography. "
    "Profile and event are in Russian."
)


def available() -> bool:
    return bool(os.environ.get("TYPESAFE_API_KEY"))


def card(e: dict) -> dict:
    return {
        "title": e["title"],
        "summary": e["summary"],
        "category": e["category"],
        "language": e.get("language", ""),
        "where": sorted({s["city"] for s in e["sessions"]})[:4],
        "price": e.get("price_text", ""),
    }


def score(events: list[dict], profile: str) -> int:
    """Ставит событиям personal (1–10) по оценке Jev. Возвращает число оценённых."""
    if not events:
        return 0
    from typesafe_sdk import Score, TypeSafeClient

    client = TypeSafeClient()
    question = Score(instructions=INSTRUCTIONS, criteria=LEVELS)
    done = 0
    for e in events:
        try:
            r = client.system_one(state={"owner_profile": profile, "event": card(e)}, questions={"interest": question})
        except Exception as err:  # noqa: BLE001 — одно сбойное событие не должно ронять весь сбор
            log.warning("Jev: не удалось оценить «%s» (%s)", e["title"], err)
            continue
        e["personal"] = max(1, min(10, round(1 + r.answers["interest"].score)))  # уровни с нуля → 1–10
        e["personal_by"] = "jev"
        done += 1
    log.info("Jev: оценено событий %d из %d", done, len(events))
    return done
