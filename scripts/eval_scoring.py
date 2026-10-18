"""Сравнение моделей для личной оценки событий: Jev, Gemini, Claude Haiku.

Проверка честная: события, которые владелец оценил (👍 = 1, 👎 = 0), делятся на 4 части;
каждую часть оценивают, показывая модели в качестве примеров только оценки из остальных частей.
Метрика — AUC: доля пар «👍-событие, 👎-событие», где 👍 получило оценку выше (0,5 — наугад, 1 — идеально).
Дополнительно каждая модель оценивает все будущие события — для топ-20 «на глаз».

Запуск: GitHub → Actions → «Сравнение моделей оценки» (ключи берутся из секретов).
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from afisha.extract import SCORING_RULES, profile_text  # noqa: E402
from afisha.main import EVENTS_PATH, load_feedback, pull_events  # noqa: E402

FOLDS = 4
BATCH = 40


def card(e: dict) -> dict:
    return {
        "id": e["id"],
        "title": e["title"],
        "summary": e["summary"],
        "category": e["category"],
        "language": e.get("language", ""),
        "where": sorted({s["city"] for s in e["sessions"]})[:4],
        "price": e.get("price_text", ""),
    }


# ---------- Модели ----------
SCORE_SCHEMA = {
    "type": "object",
    "properties": {"scores": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "personal": {"type": "integer"}},
        "required": ["id", "personal"], "additionalProperties": False,
    }}},
    "required": ["scores"], "additionalProperties": False,
}
TASK = (
    "Оцени для каждого события личную оценку personal (1–10) по профилю владельца и его примерам 👍/👎. "
    "География не учитывается. Верни JSON {\"scores\": [{\"id\", \"personal\"}]} для всех событий."
)


def haiku(profile: str, events: list[dict]) -> dict[str, float]:
    import anthropic

    client = anthropic.Anthropic()
    out: dict[str, float] = {}
    for i in range(0, len(events), BATCH):
        part = [card(e) for e in events[i : i + BATCH]]
        r = client.messages.create(
            model="claude-haiku-5-5", max_tokens=8000,
            system=SCORING_RULES + "\n\n" + profile,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCORE_SCHEMA}},
            messages=[{"role": "user", "content": TASK + "\n" + json.dumps(part, ensure_ascii=False)}],
        )
        text = next(b.text for b in r.content if b.type == "text")
        out.update({x["id"]: x["personal"] for x in json.loads(text)["scores"]})
    return out


def gemini(profile: str, events: list[dict]) -> dict[str, float]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    model = os.environ.get("GEMINI_MODEL") or gemini_model(client)
    out: dict[str, float] = {}
    for i in range(0, len(events), BATCH):
        part = [card(e) for e in events[i : i + BATCH]]
        for attempt in range(4):
            try:
                r = client.models.generate_content(
                    model=model,
                    contents=TASK + "\n" + json.dumps(part, ensure_ascii=False),
                    config=types.GenerateContentConfig(
                        system_instruction=SCORING_RULES + "\n\n" + profile,
                        response_mime_type="application/json",
                        response_json_schema=SCORE_SCHEMA,
                        temperature=0,
                    ),
                )
                out.update({x["id"]: x["personal"] for x in json.loads(r.text)["scores"]})
                break
            except Exception as err:  # noqa: BLE001 — бесплатный тариф иногда отвечает «слишком часто»
                print(f"  Gemini: повтор после ошибки {err}", file=sys.stderr)
                time.sleep(20 * (attempt + 1))
        time.sleep(7)  # бесплатный тариф: ограничение запросов в минуту
    return out


_GEMINI_MODEL: str | None = None


def gemini_model(client) -> str:
    """Самая свежая модель Gemini Flash из доступных ключу (без lite/image/tts/live)."""
    global _GEMINI_MODEL
    if _GEMINI_MODEL:
        return _GEMINI_MODEL
    names = [m.name.split("/")[-1] for m in client.models.list()]
    flash = [n for n in names if "flash" in n and not any(x in n for x in ("lite", "image", "tts", "live", "audio", "exp", "preview"))]
    flash = flash or [n for n in names if "flash" in n]
    _GEMINI_MODEL = sorted(flash)[-1]
    print(f"Gemini: модель {_GEMINI_MODEL}", file=sys.stderr)
    return _GEMINI_MODEL


JEV_LEVELS = [f"{n} — " + d for n, d in zip(range(1, 11), [
    "совсем не интересно владельцу", "почти не интересно", "скорее не интересно", "так себе",
    "нейтрально", "умеренно интересно", "интересно", "очень интересно", "обязательно стоит пойти",
    "любимый артист или событие мечты",
])]


def jev(profile: str, events: list[dict]) -> dict[str, float]:
    from typesafe_sdk import Score, TypeSafeClient

    client = TypeSafeClient()
    question = Score(
        instructions=(
            "How interesting is this cultural event to the owner of the digest, judging by the owner's profile, "
            "stated tastes and the examples they liked (👍) and disliked (👎)? Ignore geography. "
            "Profile and event are in Russian."
        ),
        criteria=JEV_LEVELS,
    )
    out: dict[str, float] = {}
    for e in events:
        r = client.system_one(state={"owner_profile": profile, "event": card(e)}, questions={"interest": question})
        out[e["id"]] = 1 + r.answers["interest"].score  # уровни с нуля → шкала 1–10
    return out


SCORERS = {"Jev": jev, "Gemini": gemini, "Claude Haiku": haiku}


# ---------- Метрики ----------
def auc(scores: dict[str, float], labels: dict[str, int]) -> float:
    pos = [scores[i] for i, y in labels.items() if y == 1 and i in scores]
    neg = [scores[i] for i, y in labels.items() if y == 0 and i in scores]
    if not pos or not neg:
        return float("nan")
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else float("nan")


def main() -> None:
    cfg = tomllib.load(open(ROOT / "config.toml", "rb"))
    pull_events()
    data = json.loads(EVENTS_PATH.read_text(encoding="utf-8"))
    events = {e["id"]: e for e in data["events"]}
    feedback = load_feedback()
    labels = {x["id"]: 1 for x in feedback.get("likes", []) if x["id"] in events}
    labels.update({x["id"]: 0 for x in feedback.get("dislikes", []) if x["id"] in events})
    ids = sorted(labels)
    random.Random(42).shuffle(ids)
    folds = [ids[k::FOLDS] for k in range(FOLDS)]
    print(f"Оценённых событий в выборке: {len(ids)} (👍 {sum(labels.values())}, 👎 {len(ids) - sum(labels.values())})")

    def profile_without(hidden: set[str]) -> str:
        fb = {
            "likes": [x for x in feedback.get("likes", []) if x["id"] not in hidden],
            "dislikes": [x for x in feedback.get("dislikes", []) if x["id"] not in hidden],
        }
        return profile_text(cfg["profile"], fb)

    upcoming = [e for e in data["events"] if e["id"] not in labels]
    report = ["# Сравнение моделей личной оценки", "",
              f"Выборка: {len(ids)} оценённых событий (👍 {sum(labels.values())}, 👎 {len(ids) - sum(labels.values())}), "
              f"{FOLDS} части; примеры для модели — только из других частей.", "",
              "| Модель | AUC (0,5 — наугад, 1 — идеально) | средняя оценка 👍 | средняя оценка 👎 | время |",
              "|---|---|---|---|---|"]
    tops = {}
    baseline = {i: events[i].get("personal", events[i]["interest"]) for i in ids}
    report.append(
        f"| Sonnet (текущие оценки, для справки*) | {auc(baseline, labels):.2f} | "
        f"{mean(baseline[i] for i in ids if labels[i]):.1f} | {mean(baseline[i] for i in ids if not labels[i]):.1f} | — |"
    )
    for name, fn in SCORERS.items():
        start = time.time()
        scores: dict[str, float] = {}
        try:
            for fold in folds:
                scores.update(fn(profile_without(set(fold)), [events[i] for i in fold]))
            full = fn(profile_without(set()), upcoming)
        except Exception as err:  # noqa: BLE001
            report.append(f"| {name} | ошибка: {err} | | | |")
            continue
        took = time.time() - start
        report.append(
            f"| {name} | {auc(scores, labels):.2f} | {mean(scores[i] for i in ids if labels[i] and i in scores):.1f} | "
            f"{mean(scores[i] for i in ids if not labels[i] and i in scores):.1f} | {took:.0f} с |"
        )
        tops[name] = sorted(upcoming, key=lambda e: -full.get(e["id"], 0))[:20]
    report += ["", "\\* Текущие оценки Sonnet ставились, когда модель видела все примеры, поэтому их AUC завышен.", ""]
    for name, top in tops.items():
        report += [f"## Топ-20: {name}", ""] + [f"{n}. {e['title']}" for n, e in enumerate(top, 1)] + [""]
    text = "\n".join(report)
    print(text)
    out = os.environ.get("GITHUB_STEP_SUMMARY")
    if out:
        Path(out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
